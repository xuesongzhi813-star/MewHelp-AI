"""Run pytest using this interpreter, redacting output before display or disk."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CommandResult:
    returncode: int
    stdout: str


def redact(text, secrets):
    for value in sorted({str(v) for v in secrets if v}, key=len, reverse=True):
        text = text.replace(value, '[REDACTED]')
    text = re.sub(r'(?i)(?:sk|pk)-lf-[0-9a-f]{8}-[0-9a-f-]{20,}', '[REDACTED]', text)
    return re.sub(r'(?i)(mysql\+pymysql://[^\s:/]+:)[^@\s]+@', r'\1[REDACTED]@', text)


def configured_secrets():
    from dotenv import dotenv_values
    source = ROOT.parents[1] if ROOT.parent.name == '.worktrees' else ROOT
    values = {**dotenv_values(source / '.env'), **os.environ}
    return [v for k, v in values.items() if v and re.search(r'key|secret|token|password|db_url', k, re.I)]


def run_command(argv, *, secrets, log_path, timeout=240):
    try:
        process = subprocess.run(argv, cwd=ROOT, shell=False, capture_output=True,
                                 text=True, encoding='utf-8', errors='replace', timeout=timeout,
                                 env={**os.environ, 'PYTHONIOENCODING':'utf-8'})
        code, output = process.returncode, process.stdout + process.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        output = (exc.stdout or b'')
        output = output.decode('utf-8', 'replace') if isinstance(output, bytes) else output
        output += '\nTest command timed out; no passing result.\n'
    output = redact(output, secrets)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output, encoding='utf-8')
    return CommandResult(code, output)


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ['--']:
        args.pop(0)
    workspace = ROOT / '.superpowers/sdd/2026-10-26-mewhelp-ch09-observability-flywheel'
    result = run_command([sys.executable, '-m', 'pytest', *args], secrets=configured_secrets(),
                         log_path=workspace / 'latest-tests.log')
    print(result.stdout[-6000:])
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
