# Settings: Vocabulary

How often the AI commentator reaches for its predefined catchphrases versus making up something original on the fly — and what those catchphrases are. This page exists only while **Settings → Application → AI features** is on.

A note at the top explains the timing: term and Default/Custom changes apply immediately; the frequency sliders apply when you click OK or Apply.

## What's on this page — Catchphrase categories

Four rows, one per category. Each has a **Default / Custom** switch, a frequency percentage, and an **Edit...** button (editing is available once the category is set to Custom):

- **Shock Exclamations** — dramatic reactions to extreme outcomes.
- **Hype Terms** — celebration lines for wins and standout performances (e.g. "YEET YEET DELETE").
- **Negative Terms** — lines for losses and underperformance.
- **Situational Slang** — terms that only trigger when specific fight conditions are met (e.g. "Bags", "Rallybot", "Siege Humping" — a decisive loss, fed rallies, enemy siege, and so on).

**How the percentage works:** lower = more original freestyle commentary; higher = more predefined terms. At 0% the AI never uses that category and always improvises; at 100% every available term in the category is offered to the AI each time.

### The Edit Vocabulary dialog

**Edit...** opens a tabbed editor for the custom terms:

- **Add / Edit / Remove** — manage terms; the ▲ ▼ buttons reorder them.
- Each term has: **Term** (the phrase itself), **Also matches** (optional alternate wording), **When to use it** (a description for the AI), and **Style** (**ALL CAPS always** or **Normal (caps optional)**).
- Situational Slang terms additionally have **Triggers when** (the fight condition) and **SparkyBot should** (what to do when it triggers) — both required.
- **Reset to Defaults** — throw away the custom list for that category and go back to SparkyBot's built-ins.

If SparkyBot ships new default terms, a "SparkyBot learned some new words!" prompt offers them — your custom lists are never overwritten silently.

## Common problems

- **Slider is grayed out** — the category is on **Default**; switch it to **Custom** to tune frequency and edit terms.
- **Commentary repeats the same phrases** — lower the category's percentage, or add more terms so there's more to choose from.
- **A custom term never appears** — the AI picks from what's offered, so nothing is guaranteed every time; raise the percentage, and for Situational Slang check its **Triggers when** condition actually happens in your fights.
- **Made a mess of a category** — **Edit... → Reset to Defaults** restores the built-in list.
