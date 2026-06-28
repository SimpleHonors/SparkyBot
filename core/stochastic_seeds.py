"""Stochastic seed pools for prompt conditioning entropy.

Inject a random (noun, register) pair into each user message to shift the
model's conditional distribution off its typical mode. Targets cross-call
template recurrence.

Combinatorial size: len(NOUNS) * len(REGISTERS) — orders of magnitude beyond
the 9-directive rotation in vocabulary_tracker._DIRECTIVES.

No external deps; pools are inlined and curated. Expand freely.
"""
from __future__ import annotations

import random
from typing import Iterable


# ---------------------------------------------------------------------------
# Concrete nouns — observational lenses.
#
# Criteria:
#   - Concrete, evocative, mundane-to-vivid
#   - Spread across domains so combinations stay surprising
#   - NOT GW2/WvW vocabulary (we want orthogonal anchors, not domain reinforcement)
#   - Avoid proper nouns and brand names
# ---------------------------------------------------------------------------

_NOUNS_NATURE = [
    "avalanche", "tide", "wildfire", "drought", "monsoon", "hailstorm",
    "fog bank", "rip current", "sandstorm", "blizzard", "downpour",
    "thunderhead", "undertow", "tsunami", "cyclone", "frostbite",
    "tar pit", "quicksand", "magma", "geyser", "sinkhole", "rockslide",
    "tidepool", "marsh", "glacier", "thicket", "canyon", "delta",
    "salt flat", "estuary", "reef", "moor", "tundra", "scree slope",
    "dust devil", "squall", "eddy", "crevasse", "lava flow", "mudslide",
    "lightning strike", "iceberg", "sea spray", "river bend",
    "treeline", "deadfall", "burr", "thorn bush", "nettle", "kudzu",
]

_NOUNS_FAUNA = [
    "wolverine", "honey badger", "octopus", "hornet", "magpie", "raven",
    "viper", "crocodile", "shark", "barracuda", "wolf pack", "stampede",
    "swarm", "anthill", "termite mound", "beehive", "wasp nest",
    "spider web", "praying mantis", "scorpion", "leech", "vulture",
    "carrion crow", "hyena", "jackal", "lamprey", "piranha", "moray eel",
    "mongoose", "weasel", "ferret", "stoat", "raptor", "falcon",
    "lobster trap", "fishing net", "minnow school", "dolphin pod",
    "elephant herd", "buffalo run", "alligator", "snapping turtle",
]

_NOUNS_INDUSTRY = [
    "foundry", "blast furnace", "rolling mill", "machine shop", "assembly line",
    "conveyor belt", "smelter", "pressure cooker", "boiler room",
    "junkyard", "scrapyard", "salvage barge", "shipyard", "drydock",
    "freight yard", "switching yard", "loading dock", "rail spur",
    "crane operator", "longshoreman", "pit crew", "deckhand",
    "wrecking ball", "jackhammer", "rivet gun", "chainsaw", "bandsaw",
    "lathe", "drill press", "welding torch", "blowtorch", "angle grinder",
    "claw hammer", "sledge", "crowbar", "vise grip", "ratchet strap",
    "pipe wrench", "torque wrench", "punch press", "stamping mill",
]

_NOUNS_DOMESTIC = [
    "kitchen sink", "junk drawer", "linen closet", "attic", "basement",
    "garage", "tool shed", "workbench", "pantry", "spice rack",
    "cutting board", "rolling pin", "cast iron skillet", "stock pot",
    "deep fryer", "meat grinder", "food processor",
    "coffee grinder", "mortar and pestle", "whisk", "tongs", "ladle",
    "colander", "strainer", "sieve", "funnel", "kitchen timer",
    "smoke alarm", "fuse box", "circuit breaker", "thermostat",
    "weathervane", "rain gutter", "drainpipe", "downspout", "screen door",
    "porch light", "fly swatter", "mousetrap", "broom", "dustpan",
    "vacuum cleaner", "snow shovel", "rake", "hoe", "wheelbarrow",
]

_NOUNS_TRANSIT = [
    "freight train", "boxcar", "caboose", "switch engine", "diesel locomotive",
    "tugboat", "barge", "ferry", "icebreaker", "trawler", "skiff",
    "garbage truck", "dump truck", "cement mixer", "tow truck", "semi truck",
    "school bus", "ambulance", "fire engine", "snowplow", "street sweeper",
    "forklift", "bulldozer", "backhoe", "skid loader", "excavator",
    "monorail", "subway car", "tram", "trolley", "stagecoach",
    "rickshaw", "tuk-tuk", "rowboat", "kayak", "canoe", "raft",
    "hot air balloon", "blimp", "glider", "biplane", "crop duster",
]

_NOUNS_OBJECTS = [
    "anvil", "horseshoe", "fishing line", "bear trap", "snare wire",
    "tripwire", "bottle rocket", "firecracker", "smoke bomb", "flare gun",
    "magnesium strip", "thermite", "kerosene lamp", "oil lantern",
    "candle wick", "matchbook", "tinder box", "flint and steel",
    "razor blade", "scalpel", "hatchet", "machete", "cleaver",
    "pickaxe", "shovel", "spade", "scythe", "pitchfork", "rake handle",
    "trowel", "auger", "wood chisel", "stone chisel", "plumb line",
    "spirit level", "tape measure", "carpenter's square", "speed square",
    "fishing reel", "tackle box", "lure", "fly rod", "bait bucket",
]

_NOUNS_MUSIC = [
    "kettle drum", "snare drum", "bass drum", "marching band",
    "string section", "horn section", "brass band", "drum line",
    "feedback loop", "blown speaker", "amp stack", "fuzz pedal",
    "wah pedal", "delay pedal", "loop pedal", "tape echo",
    "vinyl scratch", "tape hiss", "needle drop", "static",
    "off-key choir", "out-of-tune piano", "broken metronome",
    "snapped string", "rusted fret", "cracked cymbal", "muted horn",
    "discordant chord", "minor key", "dirge", "war drum", "fight song",
]

_NOUNS_KITCHEN_FOOD = [
    "boiling oil", "burnt toast", "spilled milk", "broken yolk",
    "rotting fruit", "stale bread", "moldy cheese", "freezer burn",
    "rancid butter", "sour milk", "soggy cereal", "overproofed dough",
    "bone broth", "stock reduction", "deglazed pan", "smoking pan",
    "scorched bottom", "carbon scrape", "grease fire", "hot grease",
    "brine bath", "vinegar wash", "salt cure", "smoke house",
    "cleaver cut", "filet knife", "bone saw", "marrow scoop",
    "pressure release", "kettle whistle", "rolling boil", "rapid simmer",
]

_NOUNS_WEATHER_TIME = [
    "morning fog", "afternoon glare", "evening squall", "midnight watch",
    "rush hour", "dead of night", "wee hours", "pre-dawn chill",
    "high noon", "blue hour", "golden hour", "twilight gap",
    "yesterday's weather", "tomorrow's storm", "stalled front",
    "warm front", "cold front", "occluded front", "low pressure system",
    "barometer falling", "rising tide", "neap tide", "spring tide",
    "lunar eclipse", "solar flare", "meteor shower", "northern lights",
]

_NOUNS_SCIENCE = [
    "centrifuge", "autoclave", "Bunsen burner", "test tube rack",
    "petri dish", "agar plate", "fume hood", "particle accelerator",
    "Geiger counter", "seismograph", "barometer", "anemometer",
    "wind tunnel", "wave tank", "cyclotron", "mass spectrometer",
    "electron microscope", "x-ray plate", "fluoroscope", "stethoscope",
    "pulse oximeter", "EKG strip", "lab notebook", "control sample",
    "double-blind trial", "placebo effect", "feedback loop", "homeostasis",
    "enzyme reaction", "catalyst", "isotope", "half life",
]

_NOUNS_BODY = [
    "broken rib", "bruised knuckle", "twisted ankle", "sprained wrist",
    "torn ligament", "dislocated shoulder", "concussion protocol",
    "stitched lip", "split brow", "bloody nose", "black eye",
    "raised welt", "calloused palm", "cracked tooth", "chipped tooth",
    "bone bruise", "stress fracture", "hairline crack", "deep bruise",
    "muscle cramp", "side stitch", "shin splint", "tennis elbow",
    "carpal tunnel", "frozen shoulder", "trick knee", "bad back",
    "ringing ear", "spinning room", "shaky hand", "racing pulse",
]

_NOUNS_LANDSCAPE = [
    "abandoned mine", "boarded-up house", "vacant lot", "condemned building",
    "ghost town", "company town", "mill town", "fishing village",
    "trailer park", "construction site", "demolition site", "rebar field",
    "concrete pour", "rebar cage", "scaffolding maze", "ladder rung",
    "fire escape", "elevator shaft", "service tunnel", "crawl space",
    "drainage ditch", "culvert", "spillway", "sluice gate", "dam wall",
    "lock chamber", "loading bay", "freight elevator", "service entrance",
    "back alley", "fire lane", "service road", "frontage road",
]

_NOUNS_GAMES_SPORT = [
    "hockey rink", "boxing ring", "wrestling mat", "fencing strip",
    "racetrack", "drag strip", "circuit course", "rally stage",
    "pit lane", "starting grid", "finish line", "checkered flag",
    "yellow flag", "red card", "false start", "penalty box",
    "instant replay", "photo finish", "overtime period", "sudden death",
    "buzzer beater", "Hail Mary", "fast break", "full court press",
    "fourth and inches", "two-minute warning", "garbage time", "blowout score",
    "underdog story", "Cinderella run", "dynasty year", "rebuilding season",
]

_NOUNS_TEXTILE = [
    "loom", "spinning wheel", "knitting needle", "crochet hook", "thread spool",
    "bobbin", "darning egg", "pincushion", "seam ripper", "frayed hem",
    "dropped stitch", "unraveling sweater", "snagged thread", "tangled yarn",
    "button hole", "broken zipper", "popped seam", "patchwork quilt",
    "moth-eaten wool", "warp thread", "weft thread", "shuttle loom",
    "knotted shoelace", "tangled kite string",
]

_NOUNS_OFFICE = [
    "filing cabinet", "paper shredder", "red tape", "rubber stamp",
    "ledger book", "punch clock", "time card", "paper jam", "carbon copy",
    "inbox pile", "expense report", "audit trail", "fine print",
    "expired warranty", "hold music", "busy signal", "dial tone",
    "disconnected line", "overdraft notice", "eviction notice",
    "parking ticket", "jury summons", "dead-end memo", "triplicate form",
]

_NOUNS_GAMBLING = [
    "loaded dice", "marked deck", "house edge", "all-in shove", "busted flush",
    "dead man's hand", "slot machine", "roulette wheel", "coin flip",
    "scratch ticket", "long shot", "table stakes", "last hand", "river card",
    "bad beat", "cold deck", "stacked deck", "blank tile", "empty pot",
    "drawn-out bluff", "called bluff", "pulled punch",
]

_NOUNS_PLUMBING = [
    "clogged drain", "burst pipe", "water hammer", "pressure valve",
    "relief valve", "sump pump", "grease trap", "overflow drain",
    "flooded basement", "frozen pipe", "leaky faucet", "dripping tap",
    "gas leak", "pilot light", "water main", "fire hydrant", "manhole cover",
    "storm drain", "septic tank", "backed-up sewer", "air pocket",
    "vapor lock",
]

_NOUNS_CIRCUS = [
    "tightrope", "trapeze", "safety net", "human cannonball", "knife thrower",
    "fire eater", "juggling pins", "unicycle", "house of mirrors", "carousel",
    "Ferris wheel", "bumper cars", "ring toss", "dunk tank", "big top",
    "center ring", "clown car", "plate spinner", "sword swallower",
    "contortionist", "walking stilts", "sideshow barker",
]

_NOUNS_GARDEN = [
    "compost heap", "weed patch", "overgrown garden", "thorn hedge", "bramble",
    "vine tangle", "root rot", "crop blight", "fallow field", "scarecrow",
    "garden trellis", "greenhouse", "cold frame", "seed drill", "plow furrow",
    "hayloft", "grain silo", "cattle chute", "sheep pen", "milking stall",
    "beehive frame", "pruning shears",
]

_NOUNS_MEDICAL = [
    "operating theater", "triage tent", "emergency room", "waiting room",
    "tourniquet", "defibrillator", "IV drip", "suture kit", "plaster cast",
    "wooden crutch", "hospital gurney", "stretcher", "quarantine ward",
    "isolation room", "heart monitor", "flatline", "code blue", "crash cart",
    "blood transfusion", "ICU bay", "bedside vigil", "last rites",
]

_NOUNS_FIRE = [
    "backdraft", "flashover", "smoldering ember", "banked fire",
    "controlled burn", "ember storm", "ash heap", "chimney fire",
    "bucket brigade", "fire break", "backfire", "dying coals", "hot coals",
    "slow burn", "flash point", "kindling pile", "bellows blast",
    "forge fire", "ember glow", "cinder shower",
]

NOUNS: list = sorted(set(
    _NOUNS_NATURE + _NOUNS_FAUNA + _NOUNS_INDUSTRY + _NOUNS_DOMESTIC
    + _NOUNS_TRANSIT + _NOUNS_OBJECTS + _NOUNS_MUSIC + _NOUNS_KITCHEN_FOOD
    + _NOUNS_WEATHER_TIME + _NOUNS_SCIENCE + _NOUNS_BODY + _NOUNS_LANDSCAPE
    + _NOUNS_GAMES_SPORT
    # --- new categories (orthogonal to the existing 13; no domain reinforcement) ---
    + _NOUNS_TEXTILE + _NOUNS_OFFICE + _NOUNS_GAMBLING + _NOUNS_PLUMBING
    + _NOUNS_CIRCUS + _NOUNS_GARDEN + _NOUNS_MEDICAL + _NOUNS_FIRE
))


# ---------------------------------------------------------------------------
# Rhetorical registers — voice + tone shifts.
#
# Each entry is (name, gloss). The gloss is injected into the prompt
# alongside the name so the model knows what register to adopt.
# ---------------------------------------------------------------------------

# Registers are sampled one-per-fight as "Voice for this response:" and injected
# at the END of the user message — a soft override of the system prompt's fixed
# persona ("Fast, irreverent, guild-first"). A register only does visible work if
# it pulls AWAY from that default; registers that restate the persona (blunt /
# contemptuous / breathless / aggrieved / incredulous / scolding / tabloid) were
# dropped 2026-06-03 as no-op rolls, and "apologetic" removed (collided with the
# "Don't apologize" hard rule). Each gloss below names its contrast axis.
REGISTERS: list = [
    ("understated", "say less than the fight deserves; let restraint do the work"),
    ("deadpan", "flat affect, no exclamation, treat the dramatic as routine"),
    ("regretful", "describe what happened with quiet disappointment, not heat"),
    ("clinical", "report as a coroner or forensic analyst would — distance, no editorializing"),
    ("sportscaster", "play-by-play urgency, present-tense action verbs"),
    ("gallows humor", "grim joke posture — pretend the disaster is funny"),
    ("terse", "short sentences; almost gnomic; trim every connective"),
    ("nostalgic", "as if recalling something from long ago, even if it just happened"),
    ("matter-of-fact", "bare statement of fact, no shaping or framing"),
    ("ironic", "say the opposite of what the data shows and trust the reader"),
    ("philosophical", "treat the fight as illustrating a larger pattern"),
    ("postmortem", "write as if reviewing the fight days later, with hindsight"),
    ("vindicated", "claim a quiet I-told-you-so, even on a loss"),
    ("resigned", "describe the loss or win as inevitable from the first second"),
    ("admiring (of enemy)", "give the enemy team the credit; sketch our role as supporting cast"),
    ("noir", "first-person hardboiled detective voice; weary, observational"),
    ("naturalist", "frame the squad and enemy as species behaviors observed in the wild"),
    ("logistician", "focus on supply, positioning, rotations — not heroics"),
    ("courtroom", "lay out the evidence as if making a case for or against the squad"),
    ("eulogistic", "for losses: solemn praise for what almost worked"),
    ("celebratory restraint", "for wins: acknowledge without gloating"),
    ("ambivalent", "two truths in tension — don't resolve them"),
    ("anecdotal", "lead with the single concrete moment, not the aggregate"),
    ("interrogative", "open with a question the fight answers"),
    ("conversational aside", "as if telling a friend at the bar; mid-thought, no setup"),
    ("epitaphic", "one-line summation that could be carved on a stone"),
    ("workmanlike", "no embellishment; describe like a tradesman recapping the job"),
    # --- added 2026-06-03 (all contrast-positive vs the hype/roast default) ---
    ("war correspondent", "grim dispatch from the front; dateline urgency, casualties noted plainly"),
    ("auctioneer", "breathless rapid-fire escalation; stack the facts like rising bids"),
    ("color commentator", "the analyst beside the play-by-play — patterns and why, not the action itself"),
    ("oral historian", "as if collecting the squad's account years later, quoting the survivors"),
    ("mock-heroic", "inflate a skirmish to epic register, then let the numbers deflate it"),
    ("bureaucratic", "render it as an incident report — passive voice, no blame assigned, file and forget"),
    ("superstitious", "blame the RNG gods, reset-night curses, bad omens — superstition as cope, layered over the real facts, never inventing what didn't happen"),
    ("documentarian", "fly-on-the-wall narration; observe without judgment, let the moment speak"),
    ("storyteller", "a tale with a turn — setup, complication, reversal, in three beats"),
    ("optimist's spin", "find the usable lesson even in a rout, without denying the rout"),
    ("minimalist", "image-forward, economy of line — one resonant detail and stop"),
    ("understudy", "the voice of someone who'd have called it differently, second-guessing in real time"),
    ("comedown", "the quiet honest debrief once the adrenaline burns off — candid about what went wrong, zero hype"),
]


# ---------------------------------------------------------------------------
# Sampler
# ---------------------------------------------------------------------------

def sample_seed(recent_nouns: Iterable[str] = (),
                recent_registers: Iterable[str] = (),
                rng: random.Random = None) -> dict:
    """Pick a fresh (noun, register) pair avoiding the recent ones.

    Falls back to ignoring the avoid-set if it would leave nothing eligible.

    Args:
        recent_nouns: noun strings used in the last N fights (avoid these)
        recent_registers: register names used in the last N fights (avoid these)
        rng: optional Random instance for deterministic testing

    Returns:
        {"noun": str, "register": str, "register_gloss": str}
    """
    rng = rng or random
    recent_n = set(recent_nouns or ())
    recent_r = set(recent_registers or ())

    noun_pool = [n for n in NOUNS if n not in recent_n] or NOUNS
    reg_pool = [r for r in REGISTERS if r[0] not in recent_r] or REGISTERS

    noun = rng.choice(noun_pool)
    reg_name, reg_gloss = rng.choice(reg_pool)
    return {"noun": noun, "register": reg_name, "register_gloss": reg_gloss}


def format_seed_block(seed: dict) -> str:
    """Render the seed as the prompt block injected before FIGHT DATA."""
    noun = seed["noun"]
    article = "an" if noun[:1].lower() in "aeiou" else "a"
    return (
        "OBSERVATIONAL LENS\n"
        f"Filter this fight through the image of {article} {noun}. "
        "Don't name it in the response — let it shape the texture of your "
        "observation: what you notice, what comparison comes to mind, what "
        "the action reminds you of.\n"
        f"Voice for this response: {seed['register']} — "
        f"{seed['register_gloss']}."
    )
