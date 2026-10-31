# LLM Bargaining Experiments

A Python application for bilateral bargaining experiments between a seller and a
buyer. Both roles use the same language model to negotiate the price of a business
software subscription under configurable pricing discretion, commissions, and
information conditions.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [Results](#results)
- [Negotiation protocol](#negotiation-protocol)
- [Project structure](#project-structure)

## Features

- Local dashboard for configuring experiments and monitoring progress.
- Command-line execution with concurrent trials.
- Pricing-discretion and commission grids, including a no-commission condition.
- Truncated normal or uniform buyer valuations.
- Cost-disclosure and seller-reasoning treatments.
- Saved trial plans, resumable execution, and conversation transcripts.
- CSV and JSON results with price audits and treatment-cell summaries.

## Requirements

- Python 3.10 or newer.
- An API key and model identifier for a Chat Completions-compatible endpoint.
- An endpoint that accepts system messages, `temperature`, and `max_tokens`, and
  returns text responses.

Both bargaining roles use the configured model, API key, and endpoint. API usage
is billed by the provider.

## Installation

Clone or download the repository and open a terminal in the directory containing
this README.

Create a virtual environment:

```sh
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```sh
source .venv/bin/activate
```

Install the package:

```sh
python -m pip install -e .
```

Copy [.env.example](.env.example) to `.env`:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```sh
# macOS/Linux
cp .env.example .env
```

Edit `.env` with your provider settings:

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-provider.example/v1
MODEL_ID=your-supported-chat-model
BARGAINING_RESULTS_DIR=results
```

Keep credentials in `.env` or environment variables. `.env` is excluded from
version control.

## Quick start

### Dashboard

```sh
python -m sales_experiment.dashboard --port 8010
```

Open [http://127.0.0.1:8010](http://127.0.0.1:8010), configure the experiment, and
select **Start experiment**. The form displays the number of scheduled trials.

Open a run to monitor progress, review treatment-cell summaries, filter trials,
read conversations, and download results. Run lists and status panels refresh
every eight seconds. Use **Stop experiment** and **Resume experiment** to manage
execution. Closing the browser tab does not stop an experiment.

### Command line

```sh
python -m sales_experiment.runner --config configs/baseline.json
```

The [baseline configuration](configs/baseline.json) schedules 900 trials:

- 750 trials: 5 pricing-discretion levels × 5 commission rates × 30 repetitions.
- 150 no-commission trials: 5 pricing-discretion levels × 30 repetitions.

Set `include_high_price_no_incentive` to `false` to run only the 750
positive-commission trials. Installed commands are also available as
`llm-bargaining` and `llm-bargaining-dashboard`.

## Configuration

Copy a JSON file from [configs/](configs/) and edit its parameters. Unknown keys
and invalid values are rejected when the configuration is loaded.

### Model and execution

- `model_id`: model used by both roles. Empty or omitted values use `MODEL_ID`.
- `temperature`: sampling temperature; default `0.7`.
- `repetitions`: trials per treatment cell; default `30`.
- `max_rounds`: negotiation horizon; default `100` seller–buyer rounds.
- `max_tokens`: output-token limit per API call; default `500`.
- `request_timeout`: timeout per API request in seconds; default `120`.
- `parallel_workers`: concurrent trials; default `5`, range `1–64`.
- `random_seed`: integer for reproducible valuation draws, or `null` for fresh
  draws. It does not seed model-generated responses.

Transient request failures are retried. Exhausted retries are recorded as trial
errors. Reduce concurrency if the endpoint has restrictive rate limits.

### Treatments and valuations

- `product_cost` and `reference_price`: unit cost and reference price; defaults
  `40` and `80`.
- `discounts`: pricing-discretion fractions; default `[0.1, 0.2, 0.3, 0.4, 0.5]`.
  The seller floor is `reference_price * (1 - discount)`.
- `incentives`: positive commission fractions; default `[0.1, 0.2, 0.3, 0.4, 0.5]`.
- `include_high_price_no_incentive`: add the no-commission condition; default `true`.
- `knowledge_condition`: `seller_only` for private cost or `both` to disclose cost
  to the buyer; default `seller_only`.
- `use_tom_prompt`: enable seller reasoning; default `false`.
- `wtp_distribution`: `normal` or `uniform`; default `normal`.
- `wtp_min` and `wtp_max`: valuation bounds; defaults `50` and `110`.
- `wtp_mean` and `wtp_sd`: mean and standard deviation for the truncated normal
  distribution; defaults `80` and `10`.

Valuations are sampled within the bounds, rounded to cents, and saved in the trial
plan before negotiations begin.

### Additional settings

- `reference_prices`: optional reference-price grid crossed with the treatment
  cells. An empty list uses `reference_price`.
- `fixed_min_price`: optional fixed seller floor, replacing the floor derived from
  discretion. Multiple discretion levels then repeat the same floor.
- `audit_price_ceiling`: agreement-price ceiling for monetary summaries; default
  `150`. Set to `null` to disable it or adjust it for a different price scale.
  This setting affects the audit, not negotiation decisions.

### Example configurations

Each configuration below schedules 900 trials:

- [qwen_plus_normal.json](configs/qwen_plus_normal.json): normal valuations.
- [qwen_plus_uniform.json](configs/qwen_plus_uniform.json): uniform valuations.
- [qwen_plus_normal_disclosed_cost.json](configs/qwen_plus_normal_disclosed_cost.json):
  normal valuations with cost disclosure.
- [qwen_plus_uniform_disclosed_cost.json](configs/qwen_plus_uniform_disclosed_cost.json):
  uniform valuations with cost disclosure.
- [qwen_plus_normal_reasoning.json](configs/qwen_plus_normal_reasoning.json):
  normal valuations with seller reasoning.
- [gpt_5_4_normal.json](configs/gpt_5_4_normal.json): `gpt-5.4`.
- [claude_opus_4_8_normal.json](configs/claude_opus_4_8_normal.json): `claude-opus-4-8`.
- [gpt_5_5_normal.json](configs/gpt_5_5_normal.json): `gpt-5.5`.
- [qwen3_7_plus_normal.json](configs/qwen3_7_plus_normal.json): `qwen3.7-plus`.

The `qwen_plus` configurations use `qwen-plus-2025-12-01`. Unless specified
otherwise, configurations use normal valuations, private cost, and no seller
reasoning. Replace `model_id` with a model supported by your endpoint as needed.

## Usage

### Custom paths

Both interfaces accept `--env-file`, `--results-dir`, and `--config`:

```sh
llm-bargaining --env-file "path/to/settings.env" --config "path/to/experiment.json" --results-dir "path/to/output"
```

```sh
llm-bargaining-dashboard --env-file "path/to/settings.env" --config "path/to/experiment.json" --results-dir "path/to/output" --port 8010
```

For the dashboard, `--config` prefills the experiment form.

- `.env` is loaded from the working directory unless `--env-file` is set.
- Existing environment variables override values in the environment file. A
  nonempty JSON `model_id` overrides `MODEL_ID`.
- Relative `BARGAINING_RESULTS_DIR` values resolve against the environment file's
  directory, or the working directory when no file is loaded.
- `--results-dir` overrides `BARGAINING_RESULTS_DIR`. Command-line paths are
  relative to the working directory unless absolute.
- New runs receive unique timestamp folders. Use `--run-dir` with the runner to
  choose a specific folder. A new run will not overwrite a nonempty folder.

Absolute paths and `~` are supported. Quote paths containing spaces.

### Stop and resume

Use **Stop experiment** in the dashboard, or press Ctrl+C once in the
command-line runner. Active trials save their progress, and pending trials are
not started.

Resume a saved run:

```sh
python -m sales_experiment.runner --run-dir "results/your_run_folder" --resume
```

Resuming uses the saved configuration and trial plan, preserving valuation
draws. Completed non-error trials with readable transcripts are skipped. Errors,
stopped trials, and missing or corrupt transcripts are rerun. Do not change code
or prompt templates between stopping and resuming, or run two processes against
the same run folder.

Restart the dashboard with the same results directory to manage existing runs.

### Command help

```sh
python -m sales_experiment.runner --help
python -m sales_experiment.dashboard --help
```

Runner exit codes are `0` for success, `1` for a completed run with trial errors,
`2` for configuration or setup errors, and `130` for a graceful stop.

## Results

Each run writes its files to a separate directory:

```text
results/<run_id>/
├── config.json
├── trial_plan.csv
├── status.json
├── trials.csv
├── trials.jsonl
├── price_audit.csv
├── summary_by_cell.csv
├── summary.json
├── experiment.log
└── transcripts/
    └── trial_00001.json
```

- `config.json`: resolved experiment settings.
- `trial_plan.csv`: treatment assignments and sampled valuations.
- `status.json`: execution state, progress, and active trials.
- `trials.csv`: latest result for each trial, sorted by trial index.
- `trials.jsonl`: append-only records, including earlier attempts and retries.
- `transcripts/`: raw and parsed conversations for individual trials.
- `price_audit.csv`: agreement-validity flags and screened surplus.
- `summary_by_cell.csv` and `summary.json`: treatment-cell counts and means.
- `experiment.log`: execution log for runs started through the dashboard.

Use `trials.csv` when analyzing one final observation per trial. Summaries update
during execution and cover the attempts recorded so far.

### Price audit and summaries

An agreement is valid when its price is finite, does not exceed the configured
audit ceiling, and satisfies `seller_floor <= price <= buyer_valuation`.
Constraint violations remain in the raw records.

- Agreement rates use all recorded attempts.
- Mean valid prices use valid agreements only.
- Screened earnings retain valid agreements and disagreements, with disagreements
  contributing zero. Invalid agreements and stopped trials are omitted.
- Standard-condition API errors are omitted from screened earnings;
  no-commission-condition API errors contribute zero.
- All-attempt gross earnings divide screened gross earnings by all recorded
  attempts, assigning zero to invalid agreements, errors, and stopped trials.

Floor and valuation violations can overlap. Positive-commission and
no-commission conditions are summarized separately.

## Negotiation protocol

Each trial starts with a fixed buyer opening. A round consists of a seller
proposal followed by a buyer response. Each role has an isolated conversation
history and receives only the other party's `message` field. Seller reasoning
is recorded in the transcript but is not sent to the buyer.

The buyer can continue, accept, or reject. Acceptance records the buyer's
`accepted_price`, falling back to the current seller `offer_price` when null.
Rejection ends the negotiation; reaching the round limit produces `no_deal`.
Malformed JSON or a missing message ends the trial as an error.

For a recorded agreement at price `p`:

```text
gross seller-side earnings = p - product_cost
seller commission          = incentive * gross seller-side earnings
firm earnings              = gross seller-side earnings - seller commission
buyer surplus              = buyer valuation - p
```

The no-commission condition has zero explicit commission. Non-agreements earn
zero. API fees are not deducted. Reservation bounds are conveyed through prompts;
agreement validity is checked after the negotiation.

## Project structure

```text
.
├── .env.example
├── configs/                  Experiment configurations
├── pyproject.toml            Package metadata and installed commands
├── requirements.txt          Runtime dependencies
└── sales_experiment/
    ├── config.py             Configuration and environment loading
    ├── llm.py                API client and response parsing
    ├── prompts.py            Prompt composition
    ├── reporting.py          Price audits and summaries
    ├── runner.py             Trial planning and execution
    ├── prompt_templates/     Negotiation prompts
    └── dashboard/            Local web interface
```
