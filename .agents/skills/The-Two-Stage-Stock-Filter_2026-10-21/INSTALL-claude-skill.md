# The Two-Stage Stock Filter: Claude Skill Edition
`v2.1 · October 2026 · Reset & Invest`

The same filter as the prompt file, packaged as a Claude Skill. Same rules, less pasting.

**With the prompt**, you open the .md file, copy the whole block, paste it in and add a ticker. Works in any AI assistant.

**With the skill**, you install once. After that, say "run the filter on MSFT" and Claude loads the two stages, the cutoffs, the one-yardstick rule and the scorecard format on its own.

## Installing

1. Download `two-stage-stock-filter.skill` from your files.
2. In Claude, open Settings, then Capabilities, then Skills, and upload the file.
3. Start a new conversation and say "run the two-stage filter on" plus a ticker.

If your plan or workspace doesn't show a Skills section, use the prompt file instead. The rules are identical.

## Using it

Any of these route to the filter:

- "Run the filter on Nvidia."
- "Does ASML pass my two-stage filter?"
- "Score this stock" (with a 10-Q attached).

**Turn on web search, or attach the filing.** The skill puts a source and a date on every number and refuses to fill one from memory. A filing alone can't give it the current valuation, short interest or price history, so without search those lines come back UNKNOWN and the result is marked incomplete. That's the honest answer.

**Question 4 is yours.** The skill drafts the line that would change your mind, but marks it pending until you confirm or rewrite it, and the verdict says so ("makes the list once you confirm question 4"). That's on purpose. It's the question that matters most when a stock falls, and it has to be in your words.

**Check three numbers before you trust it.** Section 5 tells you which three and where to find them in the filing.

## What it will not do

It won't tell you what to buy, sell, hold or size. It applies a checklist to public data and shows its work.

---
*Educational tools only. Not financial, investment, tax, or legal advice. AI output contains errors; verify every material figure against primary sources before it informs any decision, and consult a licensed professional for decisions specific to you.*
