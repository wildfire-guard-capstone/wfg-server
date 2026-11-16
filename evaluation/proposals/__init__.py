"""제안서 하네스: 저장된 입력으로 제안서를 다시 만들고, 알려진 버그가 몇 건 나오는지 센다.

- checks.py   제안 항목(proposal_items 모양)에서 버그 유형별 사례를 찾는다 — LLM · DB 없이 돈다
- harness.py  replay(EC2: DB에 저장된 엔진 입력 → 실제 작성기) · check(CSV · JSONL)
              · compare(고치기 전 · 후)

    python -m evaluation.proposals replay --proposals 1-11 --distinct --rebuild-items --label before
    python -m evaluation.proposals check proposal_items.csv
    python -m evaluation.proposals compare results/proposals/before results/proposals/after

EC2에서 돌리는 법 · 결과 다루기(근거 원문이 들어 있음)는 deploy/README.md 「제안서 하네스」.
"""
