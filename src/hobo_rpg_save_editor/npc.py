"""NPC reputation and quest-flag parsing and mutation helpers."""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Sequence

from . import reputation


OBSERVED_NPC_FLAG_COUNT = 788
OBSERVED_NPC_FLAG_KEY_DIGEST = (
    "63d7bdac50120b4d0ecf26b30a0f753d"
    "58871ac46d98f10759447d13c21d47aa"
)
MAX_NPC_FLAG_KEY_LENGTH = 80
CONVERSATION_JSON_MARKER = "/conversations/json/"
ENGLISH_CONVERSATION_MARKER = (
    "/conversations/_translatedjson_/en/"
)
QUEST_JSON_MARKER = "/quests/json/"
FLAG_TOKEN_PATTERN = re.compile(r"bool_([A-Za-z0-9]+)_([01])")
CAMEL_TOKEN_PATTERN = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+"
)
UNHELPFUL_TEXTS = {
    "back",
    "bye",
    "continue",
    "end",
    "leave",
    "next",
    "nothing",
    "okay",
    "ok",
}
CZECH_FLAG_GLOSSARY = {
    "adress": "address",
    "aport": "fetch",
    "architekt": "architect",
    "auta": "cars",
    "auto": "car",
    "autoradio": "car radio",
    "blahoreci": "praises",
    "brambory": "potatoes",
    "bufet": "cafeteria",
    "cajzli": "city people",
    "ceka": "waiting",
    "charitu": "charity",
    "chce": "wants",
    "cihla": "brick",
    "dal": "gave",
    "dala": "gave",
    "dali": "gave",
    "deda": "old man",
    "dekuje": "thanks",
    "diky": "thanks",
    "done": "completed",
    "do": "to",
    "dluh": "debt",
    "dostal": "received",
    "dostala": "received",
    "doutnik": "cigar",
    "ferenzovi": "to Ferenz",
    "gauner": "thug",
    "gratulace": "congratulations",
    "grznara": "Grznar",
    "hlas": "vote",
    "hleda": "looking for",
    "informace": "information",
    "info": "information",
    "je": "is",
    "jed": "poison",
    "jizdenky": "tickets",
    "kabat": "coat",
    "kamaradil": "befriended",
    "kemp": "camp",
    "kolo": "bike",
    "kol": "bikes",
    "konec": "ending",
    "konfront": "confrontation",
    "kontejner": "container",
    "koreni": "spices",
    "kral": "king",
    "krali": "the king",
    "kradeny": "stolen",
    "kradeni": "stealing",
    "kralovsky": "royal",
    "kralovske": "royal",
    "kralovani": "kingship",
    "kultu": "the cult",
    "kumbal": "storage closet",
    "kseft": "deal",
    "ma": "has",
    "mam": "I have",
    "milost": "pardon",
    "mistrovskou": "master dish",
    "mluvil": "talked",
    "mnam": "yum",
    "mstitel": "avenger",
    "na": "for",
    "nadava": "insults",
    "nalezen": "found",
    "nasrany": "angry",
    "naucil": "taught",
    "nedostal": "did not receive",
    "nechce": "does not want",
    "nezavazne": "casually",
    "nepritel": "enemy",
    "nespokojen": "dissatisfied",
    "obchoduje": "trades",
    "odmena": "reward",
    "odmenil": "rewarded",
    "odmitl": "refused",
    "odvola": "calls off",
    "odevzdano": "handed over",
    "odemcen": "unlocked",
    "okarte": "about the card",
    "okseftovani": "trading",
    "otevren": "opened",
    "oznameni": "announcement",
    "pannu": "princess",
    "pes": "dog",
    "plysaci": "stuffed toys",
    "policie": "police",
    "pochvala": "praise",
    "podekoval": "thanked",
    "podarovan": "gifted",
    "porazen": "defeated",
    "potravinovy": "food",
    "potvrdil": "confirmed",
    "povolil": "allowed",
    "pozadavek": "request",
    "pradelna": "laundry",
    "prizen": "favor",
    "prijeti": "acceptance",
    "primluvil": "put in a good word",
    "prispel": "contributed",
    "prodal": "sold",
    "progress": "progress stage",
    "progres": "progress stage",
    "prominut": "pardoned",
    "prsten": "ring",
    "prukaz": "ID card",
    "pruzkum": "survey",
    "prvni": "first",
    "psa": "the dog",
    "radost": "happy",
    "reakce": "reaction",
    "sejme": "takes down",
    "seznamil": "introduced",
    "seznameni": "introduction",
    "sikana": "bullying",
    "skakej": "jump",
    "sleva": "discount",
    "smi": "may",
    "spoluprace": "cooperation",
    "spolupracuje": "cooperates",
    "stekej": "bark",
    "stopa": "clue",
    "tajemstvi": "secret",
    "testy": "tests",
    "trenink": "training",
    "trhu": "the market",
    "uci": "teaches",
    "udobril": "reconciled",
    "udal": "reported",
    "udan": "reported",
    "udrzbar": "caretaker",
    "uklizen": "cleaned up",
    "umi": "knows how to make",
    "umrel": "died",
    "uvod": "introduction",
    "uvital": "welcomed",
    "uz": "already",
    "varic": "stove",
    "varovani": "warning",
    "varoval": "warned",
    "vecere": "dinner",
    "ventil": "valve",
    "vezeni": "prison",
    "vim": "I know",
    "vis": "you know",
    "vynadal": "scolded",
    "vykupuje": "buys",
    "vyresen": "resolved",
    "zachranen": "rescued",
    "zamek": "lock",
    "zaplatil": "paid",
    "zasilka": "shipment",
    "zastrasen": "intimidated",
    "zaznam": "record",
    "zbit": "beaten",
    "zbozi": "goods",
    "zebraku": "of the beggars",
    "zkamaradil": "befriended",
    "zle": "bad",
    "zmlacen": "beaten up",
    "znepratelil": "became enemies with",
    "zna": "knows",
    "znam": "I know",
    "zjamy": "from the pit",
    "zpet": "back",
    "zrada": "betrayal",
    "zlodej": "thief",
}


class NpcFormatError(ValueError):
    """Raised when NPC reputation or flag data is unsafe to use."""


class NpcAnnotationError(ValueError):
    """Raised when installed NPC annotation assets cannot be loaded."""


@dataclass(frozen=True)
class NpcFlagRecord:
    entry_offset: int
    key_offset: int
    value_offset: int
    key: str
    value: int


@dataclass(frozen=True)
class NpcFlagTable:
    count_offset: int
    records_start: int
    records_end: int
    records: tuple[NpcFlagRecord, ...]

    def get(self, key: str) -> NpcFlagRecord:
        matches = tuple(record for record in self.records if record.key == key)
        if len(matches) != 1:
            raise NpcFormatError(
                f"expected exactly one NPC flag named {key!r}, "
                f"found {len(matches)}"
            )
        return matches[0]


@dataclass(frozen=True)
class NpcFlagChange:
    key: str
    expected_value: int
    new_value: int


@dataclass(frozen=True)
class NpcEditPlan:
    source_sha256: str
    reputation_changes: tuple[reputation.ReputationChange, ...]
    flag_changes: tuple[NpcFlagChange, ...]


@dataclass(frozen=True)
class NpcData:
    reputation: reputation.ReputationTable
    flags: NpcFlagTable


@dataclass(frozen=True)
class NpcFlagValueChange:
    key: str
    before: int
    after: int


@dataclass(frozen=True)
class NpcFlagAnnotation:
    key: str
    associations: tuple[str, ...]
    meaning: str
    inferred: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class NpcFlagAnnotationCatalog:
    annotations: Mapping[str, NpcFlagAnnotation]
    warnings: tuple[str, ...] = ()

    def get(self, key: str) -> NpcFlagAnnotation:
        try:
            return self.annotations[key]
        except KeyError as exc:
            raise NpcAnnotationError(
                f"missing NPC flag annotation for {key!r}"
            ) from exc


@dataclass(frozen=True)
class _FlagReference:
    association: str
    text: str
    value: int
    mutates: bool
    source: str


def npc_flag_key_digest(keys: Sequence[str]) -> str:
    """Return the ordered schema fingerprint used for NPC flag validation."""
    digest = hashlib.sha256()
    for key in keys:
        raw = key.encode("ascii")
        digest.update(struct.pack("<I", len(raw)))
        digest.update(raw)
    return digest.hexdigest()


def _json_object(text: str, label: str) -> Mapping[str, object]:
    try:
        source = json.loads(text.lstrip("\ufeff"))
    except (TypeError, ValueError) as exc:
        raise NpcAnnotationError(f"{label} is not valid JSON") from exc
    if not isinstance(source, dict):
        raise NpcAnnotationError(f"{label} must contain a JSON object")
    return source


def _asset_stem(path: str) -> str:
    return Path(path).stem


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _display_words(value: str) -> str:
    words = CAMEL_TOKEN_PATTERN.findall(value.replace("_", " "))
    return " ".join(words) if words else value.replace("_", " ")


def _conversation_display_name(path: str) -> str:
    stem = _asset_stem(path)
    normalized_stem = _normalized_name(stem)
    for archetype_id, raw_name in reputation.REPUTATION_ARCHETYPE_NAMES.items():
        if _normalized_name(raw_name) == normalized_stem:
            return reputation.reputation_display_name(archetype_id)
    for prefix in ("specific_", "hobo_", "inside_", "dog_", "npc_"):
        if stem.casefold().startswith(prefix):
            stem = stem[len(prefix):]
            break
    if stem.casefold().startswith(("m_", "f_")):
        stem = stem[2:]
    return _display_words(stem).strip().title()


def _english_conversation_path(path: str) -> str:
    prefix, _, suffix = path.partition(CONVERSATION_JSON_MARKER)
    return (
        prefix
        + ENGLISH_CONVERSATION_MARKER
        + Path(suffix).name
    ).casefold()


def _localized_values(
    text: Optional[str],
    label: str,
) -> Mapping[str, str]:
    if text is None:
        return {}
    source = _json_object(text, label)
    keys = source.get("keys")
    values = source.get("values")
    if keys is None and values is None:
        return {}
    if not isinstance(keys, list) or not isinstance(values, list):
        raise NpcAnnotationError(
            f"{label} must contain keys and values lists"
        )
    if len(keys) != len(values):
        raise NpcAnnotationError(
            f"{label} keys and values have different lengths"
        )
    return {
        key: value
        for key, value in zip(keys, values)
        if isinstance(key, str) and isinstance(value, str)
    }


def _clean_context_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"<[^>]+>", "", value)
    text = " ".join(text.split()).strip()
    if not text or "_" in text:
        return ""
    normalized = text.strip("[]().! ").casefold()
    if normalized in UNHELPFUL_TEXTS:
        return ""
    if len(text) < 8:
        return ""
    if len(text) > 180:
        text = text[:177].rstrip() + "..."
    return text


def _flag_tokens(value: object) -> tuple[tuple[str, int], ...]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, list):
        strings.extend(item for item in value if isinstance(item, str))
    tokens: list[tuple[str, int]] = []
    for text in strings:
        tokens.extend(
            (match.group(1), int(match.group(2)))
            for match in FLAG_TOKEN_PATTERN.finditer(text)
        )
    return tuple(tokens)


def _walk_reference_nodes(
    value: object,
) -> Sequence[Mapping[str, object]]:
    nodes: list[Mapping[str, object]] = []
    if isinstance(value, dict):
        if any(key in value for key in ("c", "cc", "rews")):
            nodes.append(value)
        for child in value.values():
            nodes.extend(_walk_reference_nodes(child))
    elif isinstance(value, list):
        for child in value:
            nodes.extend(_walk_reference_nodes(child))
    return nodes


def _reference_text(
    node: Mapping[str, object],
    localization: Mapping[str, str],
) -> str:
    identifier = node.get("id")
    localized = (
        localization.get(identifier, "")
        if isinstance(identifier, str)
        else ""
    )
    return _clean_context_text(localized or node.get("t"))


def _references_from_asset(
    source: Mapping[str, object],
    *,
    association: str,
    localization: Mapping[str, str],
    path: str,
) -> Mapping[str, tuple[_FlagReference, ...]]:
    references: dict[str, list[_FlagReference]] = {}
    for node in _walk_reference_nodes(source):
        text = _reference_text(node, localization)
        rewards = _flag_tokens(node.get("rews"))
        conditions = (
            *_flag_tokens(node.get("c")),
            *_flag_tokens(node.get("cc")),
        )
        for key, value in rewards:
            references.setdefault(key, []).append(
                _FlagReference(
                    association=association,
                    text=text,
                    value=value,
                    mutates=True,
                    source=path,
                )
            )
        for key, value in conditions:
            references.setdefault(key, []).append(
                _FlagReference(
                    association=association,
                    text=text,
                    value=value,
                    mutates=False,
                    source=path,
                )
            )
    return {
        key: tuple(items)
        for key, items in references.items()
    }


def _quest_character_associations(
    source: Mapping[str, object],
) -> tuple[str, ...]:
    archetype_ids: set[int] = set()

    def collect(value: object) -> None:
        if isinstance(value, dict):
            archetype_id = value.get("arch")
            if isinstance(archetype_id, int):
                archetype_ids.add(archetype_id)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(source)
    return tuple(
        reputation.reputation_display_name(archetype_id)
        for archetype_id in sorted(archetype_ids)
        if archetype_id in reputation.REPUTATION_ARCHETYPE_NAMES
    )


def _merge_references(
    target: dict[str, list[_FlagReference]],
    source: Mapping[str, Sequence[_FlagReference]],
) -> None:
    for key, references in source.items():
        target.setdefault(key, []).extend(references)


def _reference_score(reference: _FlagReference) -> tuple[int, int]:
    text = reference.text
    if not text:
        return (-1, 0)
    length = len(text)
    useful_length = min(length, 130)
    mutation_bonus = 12 if reference.mutates else 0
    sentence_bonus = 8 if " " in text else 0
    return (useful_length + mutation_bonus + sentence_bonus, -length)


def _meaning_from_references(
    references: Sequence[_FlagReference],
) -> Optional[str]:
    useful = tuple(reference for reference in references if reference.text)
    if not useful:
        return None
    best = max(useful, key=_reference_score)
    if best.mutates:
        action = "Set after" if best.value else "Cleared after"
    else:
        action = "Controls dialogue"
    return f'{action}: "{best.text}"'


def _translate_flag_token(token: str) -> str:
    if token.isdigit():
        return token
    translated = CZECH_FLAG_GLOSSARY.get(token.casefold())
    if translated is not None:
        return translated
    return token.casefold()


def infer_npc_flag_meaning(key: str) -> str:
    """Return a readable, explicitly inferred rendering of one flag key."""
    tokens = CAMEL_TOKEN_PATTERN.findall(key)
    words: list[str] = []
    for token in tokens:
        translated = _translate_flag_token(token)
        if token.isdigit() and words:
            words[-1] = f"{words[-1]} {translated}"
        else:
            words.append(translated)
    phrase = " ".join(words) if words else key
    return f"{phrase[:1].upper() + phrase[1:]} (inferred from key)"


def _known_character_aliases(paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
    names = {
        _conversation_display_name(path)
        for path in paths
        if CONVERSATION_JSON_MARKER in path.casefold()
    }
    names.update(
        reputation.reputation_display_name(archetype_id)
        for archetype_id in reputation.REPUTATION_ARCHETYPE_NAMES
    )
    aliases: dict[str, str] = {}
    for name in names:
        candidates = {name, *name.split()}
        for candidate in candidates:
            alias = _normalized_name(candidate)
            if len(alias) >= 3:
                aliases.setdefault(alias, name)
    return tuple(
        sorted(
            aliases.items(),
            key=lambda item: (-len(item[0]), item[0]),
        )
    )


def _inferred_character(
    key: str,
    aliases: Sequence[tuple[str, str]],
) -> Optional[str]:
    normalized_key = _normalized_name(key)
    for alias, name in aliases:
        if normalized_key.startswith(alias):
            return name
    return None


def build_npc_flag_annotations(
    flag_keys: Sequence[str],
    assets: Mapping[str, str],
) -> NpcFlagAnnotationCatalog:
    """Build English flag annotations from extracted game TextAssets."""
    normalized_assets = {
        path.casefold(): text
        for path, text in assets.items()
    }
    references: dict[str, list[_FlagReference]] = {}
    warnings: list[str] = []
    raw_paths = tuple(
        path
        for path in assets
        if (
            CONVERSATION_JSON_MARKER in path.casefold()
            or QUEST_JSON_MARKER in path.casefold()
        )
        and "/_translatedjson_/" not in path.casefold()
    )
    aliases = _known_character_aliases(raw_paths)

    for path in raw_paths:
        try:
            source = _json_object(assets[path], path)
            if CONVERSATION_JSON_MARKER in path.casefold():
                localization_path = _english_conversation_path(path)
                localization = _localized_values(
                    normalized_assets.get(localization_path),
                    localization_path,
                )
                association = _conversation_display_name(path)
            else:
                localization = {}
                title = source.get("questTitle")
                quest_association = (
                    f"Quest: {title}"
                    if isinstance(title, str) and title.strip()
                    else f"Quest: {_display_words(_asset_stem(path))}"
                )
                associations = (
                    quest_association,
                    *_quest_character_associations(source),
                )
            if CONVERSATION_JSON_MARKER in path.casefold():
                associations = (association,)
            for current_association in associations:
                _merge_references(
                    references,
                    _references_from_asset(
                        source,
                        association=current_association,
                        localization=localization,
                        path=path,
                    ),
                )
        except NpcAnnotationError as exc:
            warnings.append(str(exc))

    annotations: dict[str, NpcFlagAnnotation] = {}
    for key in flag_keys:
        key_references = tuple(references.get(key, ()))
        associations = {
            reference.association
            for reference in key_references
        }
        inferred_character = _inferred_character(key, aliases)
        if (
            inferred_character is not None
            and inferred_character not in associations
        ):
            associations.add(f"{inferred_character} (inferred)")
        if not associations:
            associations.add("Global / quest state")
        ordered_associations = tuple(
            sorted(
                associations,
                key=lambda item: (
                    item.startswith("Quest:"),
                    item.casefold(),
                ),
            )
        )
        meaning = _meaning_from_references(key_references)
        inferred = meaning is None
        annotations[key] = NpcFlagAnnotation(
            key=key,
            associations=ordered_associations,
            meaning=meaning or infer_npc_flag_meaning(key),
            inferred=inferred,
            evidence=tuple(
                sorted(
                    {
                        reference.source
                        for reference in key_references
                    }
                )
            ),
        )

    if warnings:
        warnings = [
            f"{len(warnings)} NPC annotation assets could not be parsed; "
            "fallback labels were used where needed."
        ]
    return NpcFlagAnnotationCatalog(
        annotations=MappingProxyType(annotations),
        warnings=tuple(warnings),
    )


def fallback_npc_flag_annotations(
    flag_keys: Sequence[str],
    *,
    warning: Optional[str] = None,
) -> NpcFlagAnnotationCatalog:
    """Create key-derived annotations when installed game data is unavailable."""
    aliases = _known_character_aliases(())
    annotations: dict[str, NpcFlagAnnotation] = {}
    for key in flag_keys:
        inferred_character = _inferred_character(key, aliases)
        associations = (
            (f"{inferred_character} (inferred)",)
            if inferred_character is not None
            else ("Global / quest state",)
        )
        annotations[key] = NpcFlagAnnotation(
            key=key,
            associations=associations,
            meaning=infer_npc_flag_meaning(key),
            inferred=True,
            evidence=(),
        )
    warnings = (warning,) if warning else ()
    return NpcFlagAnnotationCatalog(
        annotations=MappingProxyType(annotations),
        warnings=warnings,
    )


def _text_asset_script(pointer: object, path: str) -> str:
    try:
        deref = getattr(pointer, "deref", None)
        reader = deref() if callable(deref) else pointer
        parse = getattr(reader, "parse_as_object", None)
        asset = parse() if callable(parse) else reader.read()
        script = asset.m_Script
    except Exception as exc:
        raise NpcAnnotationError(
            f"could not read NPC annotation asset {path}: {exc}"
        ) from exc
    if isinstance(script, bytes):
        return script.decode("utf-8-sig")
    if isinstance(script, str):
        return script.lstrip("\ufeff")
    raise NpcAnnotationError(
        f"NPC annotation asset {path} is not text"
    )


def load_npc_flag_annotations(
    game_dir: Path,
    flag_keys: Sequence[str],
) -> NpcFlagAnnotationCatalog:
    """Load installed quest and conversation data for NPC flag annotations."""
    from .inventory import (
        RESOURCE_BUNDLE_PATH,
        _unitypy_module,
    )

    bundle_path = (
        game_dir.expanduser().absolute() / RESOURCE_BUNDLE_PATH
    )
    if not bundle_path.is_file():
        raise NpcAnnotationError(f"ResourcesBundle not found: {bundle_path}")
    UnityPy = _unitypy_module()
    try:
        environment = UnityPy.load(str(bundle_path))
    except Exception as exc:
        raise NpcAnnotationError(
            f"could not open ResourcesBundle: {exc}"
        ) from exc
    container = getattr(environment, "container", None)
    items = getattr(container, "items", None)
    if not callable(items):
        raise NpcAnnotationError(
            "ResourcesBundle has no readable asset container"
        )

    assets: dict[str, str] = {}
    warnings: list[str] = []
    for path, pointer in items():
        if not isinstance(path, str):
            continue
        normalized = path.casefold()
        include = (
            (
                CONVERSATION_JSON_MARKER in normalized
                and "/_translatedjson_/" not in normalized
            )
            or ENGLISH_CONVERSATION_MARKER in normalized
            or QUEST_JSON_MARKER in normalized
        )
        if not include:
            continue
        try:
            assets[path] = _text_asset_script(pointer, path)
        except NpcAnnotationError as exc:
            warnings.append(str(exc))

    catalog = build_npc_flag_annotations(flag_keys, assets)
    combined_warnings = (*warnings, *catalog.warnings)
    if combined_warnings:
        combined_warnings = (
            "Some installed NPC annotation assets were unavailable; "
            "fallback labels were used where needed.",
        )
    return NpcFlagAnnotationCatalog(
        annotations=catalog.annotations,
        warnings=tuple(combined_warnings),
    )


def _read_7bit_int(data: bytes, offset: int) -> Optional[tuple[int, int]]:
    value = 0
    shift = 0
    for size in range(1, 6):
        if offset + size > len(data):
            return None
        byte = data[offset + size - 1]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, size
        shift += 7
    return None


def _parse_flag_record(
    data: bytes,
    offset: int,
    *,
    max_key_length: int,
) -> Optional[tuple[NpcFlagRecord, int]]:
    encoded_length = _read_7bit_int(data, offset)
    if encoded_length is None:
        return None
    key_length, length_size = encoded_length
    if not 1 <= key_length <= max_key_length:
        return None
    key_offset = offset + length_size
    key_end = key_offset + key_length
    value_offset = key_end
    next_offset = value_offset + 4
    if next_offset > len(data):
        return None
    raw_key = data[key_offset:key_end]
    if any(byte < 0x20 or byte > 0x7E for byte in raw_key):
        return None
    try:
        key = raw_key.decode("ascii")
    except UnicodeDecodeError:
        return None
    value = struct.unpack_from("<i", data, value_offset)[0]
    return (
        NpcFlagRecord(
            entry_offset=offset,
            key_offset=key_offset,
            value_offset=value_offset,
            key=key,
            value=value,
        ),
        next_offset,
    )


def find_npc_flag_tables(
    data: bytes,
    *,
    expected_count: int = OBSERVED_NPC_FLAG_COUNT,
    expected_key_digest: str = OBSERVED_NPC_FLAG_KEY_DIGEST,
    max_key_length: int = MAX_NPC_FLAG_KEY_LENGTH,
) -> tuple[NpcFlagTable, ...]:
    """Find NPC flag tables matching the complete observed ordered schema."""
    if expected_count <= 0:
        raise ValueError("expected NPC flag count must be positive")
    count_raw = struct.pack("<i", expected_count)
    tables: list[NpcFlagTable] = []
    search_offset = 0
    while True:
        count_offset = data.find(count_raw, search_offset)
        if count_offset < 0:
            break
        search_offset = count_offset + 1
        current = count_offset + 4
        records: list[NpcFlagRecord] = []
        for _ in range(expected_count):
            parsed = _parse_flag_record(
                data,
                current,
                max_key_length=max_key_length,
            )
            if parsed is None:
                break
            record, current = parsed
            records.append(record)
        if len(records) != expected_count:
            continue
        keys = tuple(record.key for record in records)
        if len(set(keys)) != expected_count:
            continue
        if npc_flag_key_digest(keys) != expected_key_digest:
            continue
        if any(record.value not in (0, 1) for record in records):
            continue
        tables.append(
            NpcFlagTable(
                count_offset=count_offset,
                records_start=count_offset + 4,
                records_end=current,
                records=tuple(records),
            )
        )
    return tuple(tables)


def parse_npc_flag_table(
    data: bytes,
    *,
    expected_count: int = OBSERVED_NPC_FLAG_COUNT,
    expected_key_digest: str = OBSERVED_NPC_FLAG_KEY_DIGEST,
) -> NpcFlagTable:
    """Return the only structurally identified NPC flag table."""
    tables = find_npc_flag_tables(
        data,
        expected_count=expected_count,
        expected_key_digest=expected_key_digest,
    )
    if len(tables) != 1:
        raise NpcFormatError(
            "expected exactly one structurally identified NPC flag table, "
            f"found {len(tables)}"
        )
    return tables[0]


def parse_npc_data(
    data: bytes,
    *,
    expected_flag_count: int = OBSERVED_NPC_FLAG_COUNT,
    expected_flag_key_digest: str = OBSERVED_NPC_FLAG_KEY_DIGEST,
) -> NpcData:
    """Parse both NPC reputation and flag structures from one character."""
    try:
        reputation_table = reputation.parse_reputation_table(data)
    except reputation.ReputationResearchError as exc:
        raise NpcFormatError(str(exc)) from exc
    return NpcData(
        reputation=reputation_table,
        flags=parse_npc_flag_table(
            data,
            expected_count=expected_flag_count,
            expected_key_digest=expected_flag_key_digest,
        ),
    )


def read_npc_data(
    path: Path,
    *,
    expected_flag_count: int = OBSERVED_NPC_FLAG_COUNT,
    expected_flag_key_digest: str = OBSERVED_NPC_FLAG_KEY_DIGEST,
) -> NpcData:
    """Read both NPC structures from one character file."""
    if not path.is_file():
        raise FileNotFoundError(path)
    return parse_npc_data(
        path.read_bytes(),
        expected_flag_count=expected_flag_count,
        expected_flag_key_digest=expected_flag_key_digest,
    )


def npc_edit_plan(
    data: bytes,
    reputation_changes: Sequence[reputation.ReputationChange] = (),
    flag_changes: Sequence[NpcFlagChange] = (),
    *,
    expected_flag_count: int = OBSERVED_NPC_FLAG_COUNT,
    expected_flag_key_digest: str = OBSERVED_NPC_FLAG_KEY_DIGEST,
) -> NpcEditPlan:
    """Create a source-bound plan for combined NPC changes."""
    parsed = parse_npc_data(
        data,
        expected_flag_count=expected_flag_count,
        expected_flag_key_digest=expected_flag_key_digest,
    )
    planned_reputation = tuple(reputation_changes)
    planned_flags = tuple(flag_changes)
    if not planned_reputation and not planned_flags:
        raise NpcFormatError("at least one NPC change is required")

    if planned_reputation:
        reputation.reputation_edit_plan(data, planned_reputation)

    seen_keys: set[str] = set()
    for change in planned_flags:
        if change.key in seen_keys:
            raise NpcFormatError(f"duplicate NPC flag change for {change.key!r}")
        seen_keys.add(change.key)
        record = parsed.flags.get(change.key)
        if record.value != change.expected_value:
            raise NpcFormatError(
                f"expected NPC flag {change.key!r} to be "
                f"{change.expected_value}, found {record.value}"
            )
        if change.new_value not in (0, 1):
            raise ValueError("NPC flag values must be 0 or 1")
        if change.new_value == change.expected_value:
            raise NpcFormatError(
                f"NPC edit plan contains a no-op flag change for {change.key!r}"
            )

    return NpcEditPlan(
        source_sha256=hashlib.sha256(data).hexdigest(),
        reputation_changes=planned_reputation,
        flag_changes=planned_flags,
    )


def apply_npc_edit_plan(
    data: bytes,
    plan: NpcEditPlan,
    *,
    expected_flag_count: int = OBSERVED_NPC_FLAG_COUNT,
    expected_flag_key_digest: str = OBSERVED_NPC_FLAG_KEY_DIGEST,
) -> bytes:
    """Apply and verify a combined source-bound NPC edit plan."""
    if hashlib.sha256(data).hexdigest() != plan.source_sha256:
        raise NpcFormatError(
            "character source changed after NPC edits were staged"
        )
    validated = npc_edit_plan(
        data,
        plan.reputation_changes,
        plan.flag_changes,
        expected_flag_count=expected_flag_count,
        expected_flag_key_digest=expected_flag_key_digest,
    )
    parsed = parse_npc_data(
        data,
        expected_flag_count=expected_flag_count,
        expected_flag_key_digest=expected_flag_key_digest,
    )
    updated = bytearray(data)
    changed_offsets: list[int] = []

    for change in validated.reputation_changes:
        record = parsed.reputation.get(change.archetype_id)
        struct.pack_into("<i", updated, record.value_offset, change.new_value)
        changed_offsets.append(record.value_offset)
    for change in validated.flag_changes:
        record = parsed.flags.get(change.key)
        struct.pack_into("<i", updated, record.value_offset, change.new_value)
        changed_offsets.append(record.value_offset)

    updated_bytes = bytes(updated)
    allowed_offsets = {
        offset + byte_index
        for offset in changed_offsets
        for byte_index in range(4)
    }
    if len(updated_bytes) != len(data):
        raise NpcFormatError("NPC update changed the character file size")
    if any(
        old != new and offset not in allowed_offsets
        for offset, (old, new) in enumerate(zip(data, updated_bytes))
    ):
        raise NpcFormatError("NPC update changed unrelated bytes")

    verified = parse_npc_data(
        updated_bytes,
        expected_flag_count=expected_flag_count,
        expected_flag_key_digest=expected_flag_key_digest,
    )
    if (
        verified.reputation.count_offset != parsed.reputation.count_offset
        or verified.flags.count_offset != parsed.flags.count_offset
        or verified.flags.records_end != parsed.flags.records_end
    ):
        raise NpcFormatError("NPC structures moved during update verification")
    for change in validated.reputation_changes:
        if (
            verified.reputation.get(change.archetype_id).value
            != change.new_value
        ):
            raise NpcFormatError(
                "NPC reputation update verification failed for archetype "
                f"{change.archetype_id}"
            )
    for change in validated.flag_changes:
        if verified.flags.get(change.key).value != change.new_value:
            raise NpcFormatError(
                f"NPC flag update verification failed for {change.key!r}"
            )
    return updated_bytes


def write_npc_edit_plan(
    path: Path,
    plan: NpcEditPlan,
    *,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
    expected_flag_count: int = OBSERVED_NPC_FLAG_COUNT,
    expected_flag_key_digest: str = OBSERVED_NPC_FLAG_KEY_DIGEST,
) -> Path:
    """Back up and atomically apply combined NPC changes."""
    from . import editor

    original = path.read_bytes()
    updated = apply_npc_edit_plan(
        original,
        plan,
        expected_flag_count=expected_flag_count,
        expected_flag_key_digest=expected_flag_key_digest,
    )
    backup_path = editor._write_updated_character(
        path,
        original,
        updated,
        now=now,
        backup_dir=backup_dir,
    )
    read_npc_data(
        path,
        expected_flag_count=expected_flag_count,
        expected_flag_key_digest=expected_flag_key_digest,
    )
    return backup_path


def compare_npc_flags(
    before_data: bytes,
    after_data: bytes,
    *,
    expected_flag_count: int = OBSERVED_NPC_FLAG_COUNT,
    expected_flag_key_digest: str = OBSERVED_NPC_FLAG_KEY_DIGEST,
) -> tuple[NpcFlagValueChange, ...]:
    """Compare structurally identified NPC flags in two captures."""
    before = parse_npc_flag_table(
        before_data,
        expected_count=expected_flag_count,
        expected_key_digest=expected_flag_key_digest,
    )
    after = parse_npc_flag_table(
        after_data,
        expected_count=expected_flag_count,
        expected_key_digest=expected_flag_key_digest,
    )
    before_by_key = {record.key: record for record in before.records}
    after_by_key = {record.key: record for record in after.records}
    if before_by_key.keys() != after_by_key.keys():
        raise NpcFormatError("NPC flag schemas differ between captures")
    return tuple(
        NpcFlagValueChange(
            key=record.key,
            before=record.value,
            after=after_by_key[record.key].value,
        )
        for record in before.records
        if record.value != after_by_key[record.key].value
    )
