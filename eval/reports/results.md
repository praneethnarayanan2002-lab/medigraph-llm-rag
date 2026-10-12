# MediGraph evaluation results

Golden set: 147 questions (`eval/golden_set.jsonl`). Gold answers come from reference Cypher queries over PrimeKG.

## Overall

| run | n | answer recall | unsupported claims ↓ | exec accuracy | retrieval recall | valid 1st try | count/yes-no | refusal correct | false refusal ↓ | fallback | p50 s | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| graphrag__qwen3.5-9b | 12 | 100.0% | 1.4% | 91.7% | 100.0% | 91.7% | – | – | 0.0% | 0.0% | 45.2 | 54.4 |

## Answer recall by category

| category | graphrag__qwen3.5-9b |
|---|---|
| drugs_for_disease | 100.0% (unsupp. 1.4%) |
