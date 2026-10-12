# MediGraph evaluation results

Golden set: 147 questions (`eval/golden_set.jsonl`). Gold answers come from reference Cypher queries over PrimeKG.

## Overall

| run | n | answer recall | unsupported claims ↓ | off-gold ↓ | exec accuracy | retrieval recall | valid 1st try | count/yes-no | refusal correct | false refusal ↓ | fallback | p50 s | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| closed_book__qwen3.5-9b | 147 | 19.8% | 39.1% | 48.4% | – | – | – | 50.0% | 25.0% | 0.0% | – | 26.2 | 149.4 |
| graphrag__llama3.1-latest | 65 | 87.8% | 0.0% | 10.8% | 76.0% | 81.0% | 88.3% | 70.0% | 100.0% | 3.3% | 23.3% | 20.9 | 167.9 |
| graphrag__qwen3.5-9b__v1-baseline | 147 | 94.0% | 0.0% | 5.2% | 91.5% | 94.7% | 93.3% | 94.4% | 100.0% | 3.0% | 1.5% | 44.5 | 109.8 |
| graphrag__qwen3.5-9b__v2-fixes | 147 | 96.8% | 0.0% | 6.2% | 93.2% | 97.0% | 94.8% | 100.0% | 100.0% | 0.7% | 1.5% | 36.9 | 104.0 |

## Answer recall by category

| category | closed_book__qwen3.5-9b | graphrag__llama3.1-latest | graphrag__qwen3.5-9b__v1-baseline | graphrag__qwen3.5-9b__v2-fixes |
|---|---|---|---|---|
| contraindications | 16.4% (unsupp. 54.8%) | 100.0% (unsupp. 0.0%) | 75.0% (unsupp. 0.0%) | 100.0% (unsupp. 0.0%) |
| count | 37.5% correct | 60.0% correct | 87.5% correct | 100.0% correct |
| disease_genes | 4.4% (unsupp. 88.9%) | 100.0% (unsupp. 0.0%) | 100.0% (unsupp. 0.0%) | 100.0% (unsupp. 0.0%) |
| disease_phenotypes | 18.8% (unsupp. 59.3%) | 86.7% (unsupp. 0.0%) | 90.7% (unsupp. 0.0%) | 91.4% (unsupp. 0.0%) |
| drug_side_effects | 16.4% (unsupp. 26.4%) | 85.7% (unsupp. 0.0%) | 100.0% (unsupp. 0.0%) | 100.0% (unsupp. 0.0%) |
| drug_targets | 0.0% (unsupp. 8.3%) | 100.0% (unsupp. 0.0%) | 93.8% (unsupp. 0.0%) | 100.0% (unsupp. 0.0%) |
| drugs_for_disease | 27.1% (unsupp. 46.3%) | 100.0% (unsupp. 0.0%) | 100.0% (unsupp. 0.0%) | 100.0% (unsupp. 0.0%) |
| follow_up | 90.0% (unsupp. 3.3%) | 85.7% (unsupp. 0.0%) | 91.4% (unsupp. 0.0%) | 91.4% (unsupp. 0.0%) |
| lay_terms | 26.6% (unsupp. 31.0%) | 100.0% (unsupp. 0.0%) | 99.1% (unsupp. 0.0%) | 99.1% (unsupp. 0.0%) |
| multihop_drugs_via_genes | 2.1% (unsupp. 37.5%) | 40.0% (unsupp. 0.0%) | 93.8% (unsupp. 0.0%) | 93.8% (unsupp. 0.0%) |
| multihop_shared_targets | 8.8% (unsupp. 28.9%) | 80.0% (unsupp. 0.0%) | 96.3% (unsupp. 0.0%) | 91.3% (unsupp. 0.0%) |
| unanswerable | 25.0% refused | 100.0% refused | 100.0% refused | 100.0% refused |
| yes_no | 60.0% correct | 80.0% correct | 100.0% correct | 100.0% correct |
