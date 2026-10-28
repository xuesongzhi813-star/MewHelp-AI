"""Run frozen, synthetic prompt samples; never prints provider configuration."""
import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def self_check_samples(output: Path):
    from dotenv import load_dotenv
    load_dotenv(ROOT.parents[1]/'.env')
    from app.config import get_settings
    from app.llm import build_workflow_model
    from app.workflow.models import ModelGateway
    from app.workflow.contracts import BudgetState
    from app.kb.self_check import PROMPT, PROMPT_VERSION, check, sufficiency_messages
    from types import SimpleNamespace
    path=ROOT/'docs/samples/ch09-self-check.jsonl'
    cases=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
    assert len(cases)==12 and len({case['id'] for case in cases})==12
    settings=get_settings().model_copy(update={'llm_temperature':0})
    gateway=ModelGateway(build_workflow_model(settings))
    rows=[]
    for case in cases:
        budget=BudgetState()
        hits=[SimpleNamespace(chunk_id=i+1,answer=text,section_path='合成验证资料')
              for i,text in enumerate(case['evidence'])]
        try:
            result=await check(gateway,sufficiency_messages(case['question'],hits),budget)
            row={**case,'actual':result.model_dump(),'passed':result.can_answer is case['expected']}
        except Exception as exc:
            row={**case,'error_type':type(exc).__name__,'passed':False}
        row['charged_tokens']=budget.charged_tokens
        rows.append(row)
        print(json.dumps({'id':case['id'],'passed':row['passed'],'error_type':row.get('error_type')},ensure_ascii=True),flush=True)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'purpose':'synthetic_prompt_evaluation',
        'prompt_version':PROMPT_VERSION,'prompt_hash':hashlib.sha256(PROMPT.encode()).hexdigest(),
        'samples_hash':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':rows,
        'passed':sum(row['passed'] for row in rows),'total':len(rows)}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report['passed']==report['total']


async def dedup_samples(output: Path):
    from dotenv import load_dotenv
    load_dotenv(ROOT.parents[1]/'.env')
    from app.config import get_settings
    from app.llm import build_workflow_model
    from app.workflow.models import ModelGateway
    from app.flywheel.normalization import ModelNormalizer,NORMALIZE_PROMPT,MATCH_PROMPT,NORMALIZATION_VERSION,MATCH_VERSION
    from app.flywheel.contracts import PoolEvent,EvidenceSnapshot
    from app.flywheel.worker import FlywheelWorker
    path=ROOT/'docs/samples/ch09-dedup.jsonl'
    cases=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
    assert len(cases)==16 and len({c['id'] for c in cases})==16
    normalizer=ModelNormalizer(ModelGateway(build_workflow_model(get_settings().model_copy(update={'llm_temperature':0}))))
    rows=[]
    for case in cases:
        event=PoolEvent(None,'self_check',case['raw'],'合成样例',EvidenceSnapshot(None,'not_retrieved',[]))
        candidates=[{'id':1,'normalized_question':case['candidate'],'review_status':case.get('status','待审')}]
        if case.get('target_page'):
            candidates=[{'id':i,'normalized_question':f'第{i}种床垫如何保养？','review_status':'通过'} for i in range(1,41)]
            candidates.append({'id':41,'normalized_question':case['candidate'],'review_status':'待审'})
        class Repo:
            def review_page(self,after=0,limit=20):return [r for r in candidates if r['id']>after][:limit]
        try:
            gap=await normalizer.normalize(event)
            match=await FlywheelWorker(normalizer,repository=Repo())._find(gap)
            preserved=all(term in gap.normalized_question for term in case['preserve'])
            row={**case,'normalized':gap.model_dump(),'actual':match.model_dump(),'preserved':preserved,
                 'passed':preserved and ((match.review_id is not None) is case['expected'])}
        except Exception as exc:
            row={**case,'error_type':type(exc).__name__,'passed':False}
        rows.append(row)
        print(json.dumps({'id':case['id'],'passed':row['passed'],'error_type':row.get('error_type')},ensure_ascii=True),flush=True)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'purpose':'synthetic_prompt_evaluation',
        'normalization_version':NORMALIZATION_VERSION,'match_version':MATCH_VERSION,
        'prompt_hash':hashlib.sha256((NORMALIZE_PROMPT+MATCH_PROMPT).encode()).hexdigest(),
        'samples_hash':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':rows,
        'passed':sum(r['passed'] for r in rows),'total':len(rows)}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report['passed']==report['total']


async def impact_samples(output: Path):
    from dotenv import load_dotenv
    load_dotenv(ROOT.parents[1]/'.env')
    from app.config import get_settings
    from app.llm import build_workflow_model
    from app.workflow.models import ModelGateway
    from app.workflow.contracts import BudgetState
    from app.evaluation.contracts import ImpactSuggestion,VersionedCase
    from app.evaluation.impact import IMPACT_PROMPT,IMPACT_VERSION,review_knowledge
    from langchain_core.messages import SystemMessage,HumanMessage
    path=ROOT/'docs/samples/ch09-label-impact.jsonl'
    cases=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
    assert len(cases)==8 and len({c['id'] for c in cases})==8
    gateway=ModelGateway(build_workflow_model(get_settings().model_copy(update={'llm_temperature':0})))
    rows=[]
    for item in cases:
        old_ref={'kind':'ch04','source':'synthetic/original.md','section_path':'合成原知识',
                 'fingerprint':'a'*64,'source_hash':'b'*64}
        case={'id':item['id'],'raw_query':item['question'],'split':item.get('split','calibration'),
            'bucket':'specific_model' if item['answerable'] else 'out_of_scope','difficulty':'medium','filters':{},
            'answerable':item['answerable'],'ground_truth_answer':item['answer'],'required_facts':item['facts'],
            'forbidden_claims':['不能保证具体到账日期','无依据终身保真承诺'],
            'relevant_chunk_refs':[old_ref] if item['answerable'] else []}
        review=review_knowledge({'id':7,'normalized_question':item['approved_question'],
            'approved_answer':item['approved_answer'],'review_status':'通过'})
        try:
            result=ImpactSuggestion.model_validate(await gateway.suggest_label_impact([
                SystemMessage(content=IMPACT_PROMPT),HumanMessage(content=json.dumps(
                    {'current_case':case,'new_knowledge':[review]},ensure_ascii=False))],BudgetState()))
            allowed=[old_ref,*[c['ref'] for c in review['chunks']]]
            source_valid=all(ref in allowed for ref in result.changes.get('relevant_chunk_refs',[]))
            VersionedCase.model_validate({**case,**result.changes})
            row={**item,'actual':result.model_dump(),'source_valid':source_valid,
                'passed':source_valid and result.affected is item['expected_affected']}
        except Exception as exc:row={**item,'error_type':type(exc).__name__,'passed':False}
        rows.append(row)
        print(json.dumps({'id':item['id'],'passed':row['passed'],'error_type':row.get('error_type')},ensure_ascii=True),flush=True)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'purpose':'synthetic_prompt_evaluation',
        'prompt_version':IMPACT_VERSION,'prompt_hash':hashlib.sha256(IMPACT_PROMPT.encode()).hexdigest(),
        'samples_hash':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':rows,'total':len(rows),
        'passed':sum(row['passed'] for row in rows)}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report['passed']==report['total']


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--kind',choices=['self-check','dedup','impact'],default='self-check')
    args=parser.parse_args()
    evaluate={'self-check':self_check_samples,'dedup':dedup_samples,'impact':impact_samples}[args.kind]
    raise SystemExit(0 if asyncio.run(evaluate(args.output)) else 1)
