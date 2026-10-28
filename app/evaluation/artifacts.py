"""Checksummed JSON written with same-volume fsync + atomic replacement."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import time


def canonical(data):
    return json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def digest(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()


class ArtifactStore:
    def __init__(self,root: Path):
        self.root=Path(root).resolve()

    def _path(self,relative):
        if (not isinstance(relative,str) or '\\' in relative or ':' in relative
                or PurePosixPath(relative).is_absolute() or not relative.endswith('.json')
                or any(not re.fullmatch(r'[A-Za-z0-9_.-]+',p) or p in ('.','..') for p in relative.split('/'))):
            raise ValueError('invalid artifact path')
        path=(self.root/relative).resolve()
        if not path.is_relative_to(self.root): raise ValueError('artifact path escape')
        return path

    def exists(self,relative):
        return self._path(relative).exists()

    def write_json(self,relative: str,data: dict):
        return self._write_json(relative,data,immutable=False)

    def _write_json(self,relative: str,data: dict,*,immutable):
        if not isinstance(data,dict): raise ValueError('artifact must be an object')
        path=self._path(relative)
        path.parent.mkdir(parents=True,exist_ok=True)
        envelope={'schema_version':1,'payload':data,'sha256':digest(data)}
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',newline='\n',dir=path.parent,
                    prefix='.writing-',suffix='.tmp',delete=False) as output:
                temporary=Path(output.name)
                output.write(canonical(envelope)+'\n');output.flush();os.fsync(output.fileno())
            for attempt in range(6):
                try:
                    if immutable:
                        try:os.link(temporary,path)
                        except FileExistsError:
                            if digest(self.read_json(relative))!=digest(data):
                                raise ValueError('immutable artifact differs') from None
                    else:os.replace(temporary,path)
                    break
                except PermissionError:
                    if attempt==5:raise
                    time.sleep(.01*(2**attempt))
        finally:
            if temporary is not None and temporary.exists(): temporary.unlink()

    def read_json(self,relative: str) -> dict:
        value=json.loads(self._path(relative).read_text(encoding='utf-8'))
        if (not isinstance(value,dict) or set(value)!={'schema_version','payload','sha256'}
                or value['schema_version']!=1 or not isinstance(value['payload'],dict)
                or digest(value['payload'])!=value['sha256']):
            raise ValueError('artifact hash or schema invalid')
        return value['payload']

    def immutable_json(self,relative,data):
        # Same-volume hard link is atomic create-if-absent on the local filesystem.
        # Never os.replace an immutable target observed absent by another writer.
        self._write_json(relative,data,immutable=True)
