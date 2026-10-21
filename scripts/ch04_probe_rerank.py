"""指定重排模型的真实合成文本探针；不触碰MySQL或Milvus。"""
import json
import time
from pathlib import Path

from app.config import get_settings
from app.kb.reranker import RerankError, rerank


def main() -> int:
    query = 'MH-DJ101的平铺胸围是多少'
    documents = [
        'MH-DJ102牛仔夹克的平铺胸围是120厘米。',
        'MH-DJ101牛仔夹克的平铺胸围是110厘米。',
        'MH-SH202衬衫是棉麻面料。',
        '积分100抵1元，有效期12个月。',
    ]
    start = time.monotonic()
    try:
        ranking = rerank(query, documents)
    except RerankError as exc:
        print(json.dumps({'error_kind': exc.kind, 'http_status': exc.status_code}, ensure_ascii=False))
        return 1
    report = {'model': get_settings().rerank_model, 'query': query, 'documents': documents,
              'ranking': [{'index': index, 'score': score} for index, score in ranking],
              'elapsed_seconds': round(time.monotonic() - start, 3),
              'passed': bool(ranking) and ranking[0][0] == 1,
              'note': '四条合成文本的接口/型号区分探针，非四策略评估报告。分数不是概率。'}
    Path('docs/samples/ch04-rerank-probe.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
