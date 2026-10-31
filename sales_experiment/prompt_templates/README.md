# Active prompt templates

The runner composes seller and buyer system prompts from these files. Only the
`message` field is sent to the other party. Seller reasoning fields remain in
the local transcript.

- `seller_body.txt` and `buyer_body.txt`: parameterized system prompts.
- `seller_schema_standard.txt` and `buyer_output_schema.txt`: JSON output schemas.
- `seller_tom.txt` and `seller_schema_tom.txt`: optional seller reasoning treatment.
- `buyer_opening.json`: standardized initial buyer message.

The supplementary no-commission wording is defined in
`sales_experiment/prompts.py`. Do not edit templates midway through a run or
before resuming it.
