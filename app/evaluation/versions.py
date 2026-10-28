"""Immutable label versions; original ch04 files remain untouched."""
import hashlib
from pathlib import Path
import re
from dataclasses import dataclass
from collections import defaultdict

from app.evaluation.artifacts import ArtifactStore,canonical,digest
from app.evaluation.contracts import DatasetVersion,VersionedCase
from app.kb.evaluation import load_cases,load_eval_manifest

EDITABLE={'answerable','ground_truth_answer','required_facts','forbidden_claims','relevant_chunk_refs'}


def labels_hash(cases):
    # Like the ch04 loader, hash normalized text with explicit LF line endings.
    return hashlib.sha256(''.join(canonical(c.model_dump(mode='json'))+'\n' for c in cases).encode()).hexdigest()


def version_name(lh,parent=None,confirmation=None):
    identity=lh if parent is None else digest({'labels_hash':lh,'parent':parent,'confirmation':confirmation['digest']})
    return 'labels-'+identity[:20]


def validate_edits(version,draft,edits,unchanged):
    by_id={c.id:c for c in version.cases}
    ids=[e.get('case_id') for e in edits]
    if (len(ids)!=len(set(ids)) or len(unchanged)!=len(set(unchanged)) or set(ids)&set(unchanged)
            or set(ids)|set(unchanged)!=set(by_id)):
        raise ValueError('confirmation coverage must explicitly include all 96 cases')
    allowed={canonical(ref.model_dump(mode='json')) for c in version.cases for ref in c.relevant_chunk_refs}
    allowed.update(canonical(c['ref']) for review in draft.reviews for c in review['chunks'])
    changed={};differences=[]
    for edit in edits:
        if set(edit)!={'case_id','changes','reason'} or not str(edit['reason']).strip():
            raise ValueError('edit requires case, field changes and human reason')
        if not isinstance(edit['changes'],dict) or not set(edit['changes'])<=EDITABLE:
            raise ValueError('case identity and split are immutable')
        old=by_id[edit['case_id']]
        candidate=VersionedCase.model_validate({**old.model_dump(mode='json'),**edit['changes']})
        if any(canonical(ref.model_dump(mode='json')) not in allowed for ref in candidate.relevant_chunk_refs):
            raise ValueError('unapproved or missing source')
        delta={key:{'old':old.model_dump(mode='json')[key],'new':candidate.model_dump(mode='json')[key]}
               for key in EDITABLE if getattr(old,key)!=getattr(candidate,key)}
        if delta:
            changed[candidate.id]=candidate
            differences.append({'case_id':candidate.id,'fields':delta,'reason':edit['reason']})
    cases=tuple(changed.get(c.id,c) for c in version.cases)
    if {c.answerable for c in cases if c.split=='calibration'}!={True,False}:
        raise ValueError('calibration must retain both labels before publication')
    return cases,differences


class VersionStore:
    def __init__(self,root: Path):
        self.artifacts=ArtifactStore(root)

    @staticmethod
    def _name(version):
        if not isinstance(version,str) or not re.fullmatch(r'labels-[0-9a-f]{20}',version):
            raise ValueError('invalid dataset version')
        return f'eval_versions/{version}/dataset.json'

    def _save(self,version):
        self.artifacts.immutable_json(self._name(version.version),version.model_dump(mode='json'))
        return self.load(version.version)

    def bootstrap(self,ch04_root: Path):
        original=load_cases(ch04_root)
        manifest=load_eval_manifest(ch04_root)
        for path,expected in manifest['source_hashes'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=expected:
                raise ValueError('baseline source hash drift')
        cases=tuple(VersionedCase.model_validate({**case.model_dump(mode='json'),
            'relevant_chunk_refs':[{'kind':'ch04',**ref.model_dump()} for ref in case.relevant_chunk_refs]}) for case in original)
        lh=labels_hash(cases);name=version_name(lh)
        return self._save(DatasetVersion(version=name,parent_version=None,baseline_version=name,
            original_labels_hash=manifest['cases_sha256'],labels_hash=lh,
            split_hash=digest({'calibration':manifest['calibration'],'test':manifest['test']}),
            calibration_ids=tuple(manifest['calibration']),test_ids=tuple(manifest['test']),cases=cases,
            source_hashes=manifest['source_hashes'],validation={'count':96,'calibration':24,'test':72,'sources_checked':True}))

    def load(self,version: str):
        data=DatasetVersion.model_validate(self.artifacts.read_json(self._name(version)))
        if (data.version!=version or labels_hash(data.cases)!=data.labels_hash
                or version!=version_name(data.labels_hash,data.parent_version,data.confirmation)):
            raise ValueError('dataset identity or labels hash invalid')
        if (len(data.cases)!=96 or len({c.id for c in data.cases})!=96 or len({c.raw_query for c in data.cases})!=96
                or len(data.calibration_ids)!=24 or len(data.test_ids)!=72
                or set(data.calibration_ids)&set(data.test_ids)
                or {c.id for c in data.cases if c.split=='calibration'}!=set(data.calibration_ids)
                or {c.id for c in data.cases if c.split=='test'}!=set(data.test_ids)
                or digest({'calibration':data.calibration_ids,'test':data.test_ids})!=data.split_hash):
            raise ValueError('dataset fixed identity/split invalid')
        if data.parent_version is not None:
            base=DatasetVersion.model_validate(self.artifacts.read_json(self._name(data.baseline_version)))
            identity=lambda c:(c.id,c.raw_query,c.bucket,c.difficulty,c.split,c.filters)
            if (list(map(identity,data.cases))!=list(map(identity,base.cases)) or data.split_hash!=base.split_hash
                    or data.original_labels_hash!=base.original_labels_hash or data.confirmation is None):
                raise ValueError('original dataset identity changed')
            from app.evaluation.contracts import ConfirmedImpact,ImpactDraft
            confirmed=ConfirmedImpact.model_validate(data.confirmation)
            saved=ConfirmedImpact.model_validate(self.artifacts.read_json(f'impacts/{confirmed.digest}/confirmation.json'))
            draft=ImpactDraft.model_validate(self.artifacts.read_json(f'impacts/{confirmed.digest}/draft.json'))
            if saved!=confirmed or digest(draft.reviews)!=confirmed.reviews_hash:
                raise ValueError('dataset confirmation provenance missing or invalid')
        return data

    def freeze(self,parent: str,draft,confirmation):
        from app.evaluation.contracts import ConfirmedImpact
        if confirmation is None: raise ValueError('human confirmation required')
        confirmed=ConfirmedImpact.model_validate(confirmation)
        saved=ConfirmedImpact.model_validate(self.artifacts.read_json(f'impacts/{confirmed.digest}/confirmation.json'))
        if (saved!=confirmed or confirmed.digest!=draft.digest or confirmed.base!=draft.base
                or draft.base['dataset_version']!=parent or confirmed.reviews_hash!=digest(draft.reviews)):
            raise ValueError('confirmation identity invalid')
        version=self.load(parent)
        cases,differences=validate_edits(version,draft,confirmed.edits,confirmed.unchanged_case_ids)
        if not differences: return version
        lh=labels_hash(cases)
        return self._save(DatasetVersion(**{**version.model_dump(mode='json'),
            'version':version_name(lh,parent,confirmed.model_dump()),'parent_version':parent,'labels_hash':lh,'cases':cases,
            'changed_case_ids':tuple(d['case_id'] for d in differences),'differences':tuple(differences),
            'confirmation':confirmed.model_dump(mode='json')}))


@dataclass(frozen=True)
class BoundVersionedCase:
    case: VersionedCase
    relevant_ids: frozenset[int]


def review_binding_key(ref):
    return digest(ref.model_dump(mode='json') if hasattr(ref,'model_dump') else ref)


def bind_version(version,sources,stored,*,review_contents,review_bindings,vector_ids):
    """Read-only strict source binding, including reuse of an existing MySQL row."""
    from app.kb.documents import build_chunks,chunk_fingerprint
    source_hashes={source.path.as_posix():hashlib.sha256(source.path.read_bytes()).hexdigest() for source in sources}
    if source_hashes!=version.source_hashes:raise ValueError('version source hash drift')
    canonical_chunks=defaultdict(list)
    for source in sources:
        for chunk in build_chunks(source.path.read_text(encoding='utf-8'),source):
            canonical_chunks[(source.path.as_posix(),chunk.section_path,chunk_fingerprint(chunk.questions,chunk.answer))].append(chunk)
    by_id=defaultdict(list);by_source=defaultdict(list)
    for row in stored:
        by_id[row.id].append(row)
        by_source[(row.section_path,chunk_fingerprint(row.questions,row.answer),row.category,row.content_type)].append(row)
    approved=defaultdict(list)
    for review in review_contents:
        for chunk in review['chunks']:approved[review_binding_key(chunk['ref'])].append(chunk)
    bound=[]
    for case in version.cases:
        ids=set()
        for ref in case.relevant_chunk_refs:
            if ref.kind=='ch04':
                if source_hashes.get(ref.source)!=ref.source_hash:raise ValueError('file source hash mismatch')
                chunks=canonical_chunks[(ref.source,ref.section_path,ref.fingerprint)]
                if len(chunks)!=1:raise ValueError('file source binding missing or ambiguous')
                chunk=chunks[0]
                rows=by_source[(ref.section_path,ref.fingerprint,chunk.category,chunk.content_type)]
            else:
                key=review_binding_key(ref)
                chunks=approved[key]
                if len(chunks)!=1 or key not in review_bindings:raise ValueError('review source binding missing or ambiguous')
                rows=by_id[review_bindings[key]]
                if len(rows)==1 and chunk_fingerprint(rows[0].questions,rows[0].answer)!=ref.fingerprint:
                    raise ValueError('review source fingerprint mismatch')
            if len(rows)!=1 or rows[0].vectorize_status!='done' or rows[0].id not in vector_ids:
                raise ValueError('MySQL/vector source binding missing, ambiguous or incomplete')
            ids.add(rows[0].id)
        bound.append(BoundVersionedCase(case,frozenset(ids)))
    return bound
