
# Groq Model Pricing

**Last verified:** 2026-11-09  
**Provider:** GroqCloud  
**Model:** `qwen/qwen3.8-27b`  
**Currency:** USD

## 1. Published Model Pricing

According to the official Groq model documentation:

| Token type | Price per 1 million tokens |
|---|---:|
| Input tokens | $0.80 |
| Output tokens | $4.00 |

Official references:

- Model documentation:
  https://console.groq.com/docs/model/qwen/qwen3.8-27b

- Supported models and pricing:
  https://console.groq.com/docs/models

## 2. Cost Calculation

The cost-equivalent calculation is:

Input cost:

    input_tokens × (0.80 / 1,000,000)

Output cost:

    output_tokens × (4.00 / 1,000,000)

Total cost:

    total_cost = input_cost + output_cost

For 1,000 answers:

    cost_per_1000_answers =
        (total_input_tokens × 0.80
        + total_output_tokens × 4.00) / 1,000,000

Here, total_input_tokens and total_output_tokens are the
aggregated usage values over the 1,000 requests.

All intermediate model calls in a pipeline request must be
included in the usage totals.

## 3. Cost Accounting Rules

- Use the API response's actual usage fields.
- Include usage from every model call belonging to a request.
- Do not use requested max_tokens as actual output usage.
- Apply Groq pricing only to real Groq calls.
- Do not apply Groq pricing to stand-in requests.
- Do not invent usage for failed or interrupted calls.

## 4. Free-Tier Considerations

This project uses the Groq free-tier allowance for real experiments.

The published rates above are reference prices for estimating
the equivalent pay-as-you-go cost of model usage.

They do not establish the actual amount charged to the account.

Actual billed expenditure must be reported separately from
theoretical list-price-equivalent cost.

If billing information is unavailable, actual expenditure
should be reported as:

    Not measured

## 5. Accounting Formula for Results

The results pipeline should calculate cost using aggregate
real-call usage from results.csv.

For stand-in experiments:

    Cost = Not applicable (stand-in)

For real experiments:

    Cost = calculated list-price equivalent

Every reported cost should specify whether it represents
actual expenditure or list-price-equivalent expenditure.
