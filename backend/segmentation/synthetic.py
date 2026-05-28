from __future__ import annotations

from backend.segmentation.models import SyntheticSegmentationCase


OFFICIAL_SOURCE_GUARD_TOKENS = [
    "call me",
    "falcon",
    "james",
    "lady bird",
    "nala",
]


def list_synthetic_cases() -> list[SyntheticSegmentationCase]:
    return [
        build_synthetic_case("pause_overlap_repair"),
        build_synthetic_case("redaction_omission_nonverbal"),
    ]


def build_synthetic_case(case_id: str) -> SyntheticSegmentationCase:
    cases = {
        "pause_overlap_repair": _pause_overlap_repair_case,
        "redaction_omission_nonverbal": _redaction_omission_nonverbal_case,
    }
    try:
        return cases[case_id]()
    except KeyError as exc:
        raise ValueError(f"Unknown synthetic segmentation case: {case_id}") from exc


def _pause_overlap_repair_case() -> SyntheticSegmentationCase:
    event_texts = [
        ("P", "Good morning, Mira."),
        ("P", "Uh, I mean, we can start."),
        ("Av", "Yes, begin here."),
        ("P", "I want"),
        ("Av", "And then we lift the cup."),
        ("P", "The blue cup is beside the plate."),
        ("Av", "Show me how you would pick it up."),
        ("P", "I reach with this hand."),
        ("Av", "Good, keep going."),
        ("P", "Um, the handle is slippery."),
        ("Av", "You can pause before lifting."),
        ("P", "I am holding it now."),
        ("Av", "Tell me what happens next."),
        ("P", "The water might spill."),
        ("Av", "Move it slowly toward the tray."),
        ("P", "I put it near the towel."),
        ("Av", "What do you notice?"),
        ("P", "It feels lighter."),
        ("Av", "Try the spoon now."),
        ("P", "Uh, I forgot the spoon."),
        ("Av", "It is on your left."),
        ("P", "I see it beside the napkin."),
        ("Av", "Use a full sentence if you can."),
        ("P", "I am lifting the spoon."),
        ("Av", "Good repair."),
        ("P", "The spoon is small."),
        ("Av", "Place it inside the cup."),
        ("P", "It makes a sound."),
        ("Av", "Tell me about the sound."),
        ("P", "It is a soft tap."),
        ("Av", "Now move the cup again."),
        ("P", "I slide it carefully."),
        ("Av", "Where did it go?"),
        ("P", "It moved toward the tray."),
        ("Av", "Do you want to stop?"),
        ("P", "No, I can continue."),
        ("Av", "Then describe the towel."),
        ("P", "The towel is folded."),
        ("Av", "Put the spoon on it."),
        ("P", "I put it there."),
        ("Av", "What did you do first?"),
        ("P", "I picked up the cup."),
        ("Av", "What did you do second?"),
        ("P", "I moved the spoon."),
        ("Av", "What was hard?"),
        ("P", "The handle was hard to hold."),
        ("Av", "Try one more movement."),
        ("P", "I turn the cup toward you."),
        ("Av", "That is clear."),
        ("P", "Uh, I almost dropped it."),
        ("Av", "You corrected it."),
        ("P", "I kept it on the table."),
        ("Av", "Say the whole action."),
        ("P", "I moved the cup to the tray."),
        ("Av", "Now finish the task."),
        ("P", "I place the cup down."),
        ("Av", "What is next to it?"),
        ("P", "The spoon is next to it."),
        ("Av", "What color is the cup?"),
        ("P", "It is blue."),
        ("Av", "What color is the towel?"),
        ("P", "It is white."),
        ("Av", "Tell me if you need help."),
        ("P", "I do not need help."),
        ("Av", "Then close the activity."),
        ("P", "I am finished."),
    ]
    descript_text = _descript_text(event_texts)
    gold_text = _gold_text(
        "Synthetic scenario: Sunrise kitchen",
        [
            "-0:00",
            "P: Good morning, Mira>",
            "P: <([FP]) I mean, we can start.>",
            "; :02",
            "Av: Yes, begin here.",
        ]
        + _gold_speaker_lines(event_texts[3:]),
    )
    return SyntheticSegmentationCase(
        case_id="pause_overlap_repair",
        title="Synthetic scenario: Sunrise kitchen",
        descript_text=descript_text,
        gold_text=gold_text,
        rule_ids=[
            "speaker-markers",
            "timestamp-markers",
            "pause-markers",
            "filled-pauses",
            "overlap-markers",
            "abandoned-utterance",
        ],
        official_source_guard_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
    )


def _redaction_omission_nonverbal_case() -> SyntheticSegmentationCase:
    event_texts = [
        ("P", "This is [redacted]."),
        ("Av", "Uh, look at the blue cup."),
        ("P", "I wa want the flower."),
        ("AvN", "points to shelf."),
        ("Av", "Yes, choose one."),
        ("P", "Okay."),
        ("Av", "Tell me what you picked."),
        ("P", "I picked the paper flower."),
        ("Av", "Where was it?"),
        ("P", "It was by the small card."),
        ("PN", "nods yes."),
        ("Av", "Use your words too."),
        ("P", "I nodded because I found it."),
        ("Av", "What should happen next?"),
        ("P", "I put it in the basket."),
        ("Av", "Describe the basket."),
        ("P", "It is round and soft."),
        ("Av", "What is beside it?"),
        ("P", "There is a green block."),
        ("Av", "Move the block first."),
        ("P", "I move it to the side."),
        ("Av", "Now try the flower."),
        ("P", "I place the flower inside."),
        ("Av", "Did it fit?"),
        ("P", "Yes, it fit."),
        ("Av", "Tell me why."),
        ("P", "Because the basket is wide."),
        ("Av", "What did you say before?"),
        ("P", "I said I wanted the flower."),
        ("Av", "What sound did you hear?"),
        ("P", "I heard a soft scrape."),
        ("Av", "Touch the card."),
        ("P", "I touch the small card."),
        ("Av", "Read the first mark."),
        ("P", "It has two lines."),
        ("AvN", "points to card corner."),
        ("P", "I see the corner."),
        ("Av", "What changed?"),
        ("P", "The card moved."),
        ("Av", "Put it back."),
        ("P", "I put it back."),
        ("Av", "What is missing?"),
        ("P", "The picture is missing."),
        ("Av", "Say that again slowly."),
        ("P", "The picture is missing."),
        ("Av", "Good, continue."),
        ("P", "I check the tray."),
        ("Av", "What do you find?"),
        ("P", "I find another flower."),
        ("Av", "Compare them."),
        ("P", "This flower is bigger."),
        ("Av", "Which one do you prefer?"),
        ("P", "I prefer the smaller one."),
        ("Av", "Place it near the basket."),
        ("P", "I place it near the basket."),
        ("Av", "What should I write down?"),
        ("P", "Write that I chose the flower."),
        ("Av", "What else?"),
        ("P", "Write that I moved the card."),
        ("Av", "Last step."),
        ("P", "I put the flower away."),
        ("Av", "Are you finished?"),
        ("P", "Yes, I am finished."),
        ("PN", "smiles."),
        ("Av", "Thank you for explaining."),
        ("P", "You are welcome."),
    ]
    descript_text = _descript_text(event_texts)
    gold_text = _gold_text(
        "Synthetic scenario: Garden practice",
        [
            "-0:00",
            "P: This is {redacted}.",
            "; :02",
            "Av: ([FP]) look at the blue cup.",
            "P: I wa* want the flower /*.",
            "AvN: {AvN: points to shelf.}",
        ]
        + _gold_speaker_lines(event_texts[4:]),
    )
    return SyntheticSegmentationCase(
        case_id="redaction_omission_nonverbal",
        title="Synthetic scenario: Garden practice",
        descript_text=descript_text,
        gold_text=gold_text,
        rule_ids=[
            "speaker-markers",
            "timestamp-markers",
            "pause-markers",
            "filled-pauses",
            "redaction-comments",
            "omission-markers",
            "communicative-nonverbal",
        ],
        official_source_guard_tokens=OFFICIAL_SOURCE_GUARD_TOKENS,
    )


def _descript_text(event_texts: list[tuple[str, str]]) -> str:
    return "\n".join(
        f"[{_clock(index * 2)}] {speaker}: {text}"
        for index, (speaker, text) in enumerate(event_texts)
    )


def _gold_text(title: str, lines: list[str]) -> str:
    return "\n".join([title, *lines])


def _gold_speaker_lines(event_texts: list[tuple[str, str]]) -> list[str]:
    lines: list[str] = []
    for index, (speaker, text) in enumerate(event_texts, start=2):
        if index % 8 == 0:
            lines.append(f"-0:{index * 2:02d}")
        elif index % 3 == 0:
            lines.append("; :02")
        if speaker in {"AvN", "PN"}:
            lines.append(f"{speaker}: {{{speaker}: {text}}}")
        else:
            lines.append(f"{speaker}: {text}")
    return lines


def _clock(total_seconds: int) -> str:
    minutes, seconds = divmod(total_seconds, 60)
    return f"00:{minutes:02d}:{seconds:02d}"
