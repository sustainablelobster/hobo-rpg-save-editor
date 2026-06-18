"""Read-only quest catalog extraction and save correlation helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Sequence

from . import npc


QUEST_STATUS_NO_FLAGS = "No save flags"
QUEST_STATUS_NOT_STARTED = "Not started"
QUEST_STATUS_IN_PROGRESS = "In progress"
QUEST_STATUS_COMPLETED = "Completed?"
QUEST_STATUS_CONFLICTING = "Conflicting / unknown"

QUEST_TYPE_QUEST = "Quest"
QUEST_TYPE_REPEATABLE = "Repeatable / temp"


class QuestCatalogError(ValueError):
    """Raised when installed quest assets cannot be loaded."""


@dataclass(frozen=True)
class QuestFlagReference:
    key: str
    value: int
    role: str
    text: str


@dataclass(frozen=True)
class QuestDefinition:
    asset_path: str
    asset_name: str
    title: str
    quest_id: Optional[str]
    type_label: str
    associations: tuple[str, ...]
    references: tuple[QuestFlagReference, ...]
    evidence: tuple[str, ...]

    @property
    def is_repeatable(self) -> bool:
        return self.type_label == QUEST_TYPE_REPEATABLE


@dataclass(frozen=True)
class QuestCatalog:
    quests: tuple[QuestDefinition, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuestProgress:
    status: str
    known_flags: tuple[str, ...]
    enabled_flags: tuple[str, ...]
    disabled_flags: tuple[str, ...]
    missing_flags: tuple[str, ...]
    reason: str

    @property
    def enabled_count(self) -> int:
        return len(self.enabled_flags)

    @property
    def total_count(self) -> int:
        return len(self.known_flags)

    @property
    def flag_summary(self) -> str:
        if not self.known_flags:
            return "0/0"
        return f"{self.enabled_count}/{self.total_count}"


@dataclass(frozen=True)
class QuestState:
    definition: QuestDefinition
    progress: QuestProgress


def _json_object(text: str, label: str) -> Mapping[str, object]:
    try:
        source = json.loads(text.lstrip("\ufeff"))
    except (TypeError, ValueError) as exc:
        raise QuestCatalogError(f"{label} is not valid JSON") from exc
    if not isinstance(source, dict):
        raise QuestCatalogError(f"{label} must contain a JSON object")
    return source


def _asset_stem(path: str) -> str:
    return Path(path).stem


def _display_words(value: str) -> str:
    words = npc.CAMEL_TOKEN_PATTERN.findall(value.replace("_", " "))
    return " ".join(words) if words else value.replace("_", " ")


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"<[^>]+>", "", value)
    text = " ".join(text.split()).strip()
    if not text or "_" in text:
        return ""
    normalized = text.strip("[]().! ").casefold()
    if normalized in npc.UNHELPFUL_TEXTS:
        return ""
    if len(text) < 8:
        return ""
    if len(text) > 180:
        return text[:177].rstrip() + "..."
    return text


def _walk(value: object) -> Sequence[Mapping[str, object]]:
    nodes: list[Mapping[str, object]] = []
    if isinstance(value, dict):
        nodes.append(value)
        for child in value.values():
            nodes.extend(_walk(child))
    elif isinstance(value, list):
        for child in value:
            nodes.extend(_walk(child))
    return nodes


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
            for match in npc.FLAG_TOKEN_PATTERN.finditer(text)
        )
    return tuple(tokens)


def _reference_text(node: Mapping[str, object]) -> str:
    for key in ("note", "t", "qInf"):
        text = _clean_text(node.get(key))
        if text:
            return text
    return ""


def _references_from_node(
    node: Mapping[str, object],
) -> tuple[QuestFlagReference, ...]:
    text = _reference_text(node)
    references: list[QuestFlagReference] = []
    for field, role in (
        ("rews", "sets"),
        ("c", "requires"),
        ("cc", "requires"),
    ):
        references.extend(
            QuestFlagReference(key=key, value=value, role=role, text=text)
            for key, value in _flag_tokens(node.get(field))
        )
    if references:
        return tuple(references)

    generic: list[QuestFlagReference] = []
    for value in node.values():
        generic.extend(
            QuestFlagReference(
                key=key,
                value=flag_value,
                role="references",
                text=text,
            )
            for key, flag_value in _flag_tokens(value)
        )
    return tuple(generic)


def _quest_references(
    source: Mapping[str, object],
) -> tuple[QuestFlagReference, ...]:
    references: dict[tuple[str, int, str, str], QuestFlagReference] = {}
    for node in _walk(source):
        for reference in _references_from_node(node):
            references.setdefault(
                (
                    reference.key,
                    reference.value,
                    reference.role,
                    reference.text,
                ),
                reference,
            )
    return tuple(
        sorted(
            references.values(),
            key=lambda item: (
                item.key.casefold(),
                item.value,
                item.role,
                item.text.casefold(),
            ),
        )
    )


def _quest_evidence(
    references: Sequence[QuestFlagReference],
) -> tuple[str, ...]:
    evidence: list[str] = []
    seen: set[str] = set()
    for reference in references:
        if not reference.text:
            continue
        if reference.text.casefold() in seen:
            continue
        seen.add(reference.text.casefold())
        evidence.append(reference.text)
        if len(evidence) >= 8:
            break
    return tuple(evidence)


def _quest_title(source: Mapping[str, object], path: str) -> str:
    title = source.get("questTitle")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return _display_words(_asset_stem(path)).strip().title()


def _quest_id(source: Mapping[str, object]) -> Optional[str]:
    value = source.get("questID")
    if isinstance(value, (str, int)):
        return str(value)
    return None


def _quest_type(source: Mapping[str, object], title: str, path: str) -> str:
    stem = _asset_stem(path).casefold()
    if source.get("isPermanent") is True:
        return QUEST_TYPE_REPEATABLE
    if title.casefold().startswith("temp work"):
        return QUEST_TYPE_REPEATABLE
    if stem.startswith("brigada"):
        return QUEST_TYPE_REPEATABLE
    return QUEST_TYPE_QUEST


def build_quest_catalog(assets: Mapping[str, str]) -> QuestCatalog:
    """Build a read-only quest catalog from extracted quest TextAssets."""
    quests: list[QuestDefinition] = []
    warnings: list[str] = []
    quest_paths = sorted(
        path
        for path in assets
        if npc.QUEST_JSON_MARKER in path.casefold()
    )
    for path in quest_paths:
        try:
            source = _json_object(assets[path], path)
        except QuestCatalogError as exc:
            warnings.append(str(exc))
            continue
        title = _quest_title(source, path)
        references = _quest_references(source)
        associations = npc._quest_character_associations(source)
        quests.append(
            QuestDefinition(
                asset_path=path,
                asset_name=_asset_stem(path),
                title=title,
                quest_id=_quest_id(source),
                type_label=_quest_type(source, title, path),
                associations=associations,
                references=references,
                evidence=_quest_evidence(references),
            )
        )
    return QuestCatalog(
        quests=tuple(
            sorted(
                quests,
                key=lambda quest: (
                    quest.title.casefold(),
                    quest.asset_name.casefold(),
                ),
            )
        ),
        warnings=tuple(warnings),
    )


def _text_asset_script(pointer: object, path: str) -> str:
    try:
        deref = getattr(pointer, "deref", None)
        reader = deref() if callable(deref) else pointer
        parse = getattr(reader, "parse_as_object", None)
        asset = parse() if callable(parse) else reader.read()
        script = asset.m_Script
    except Exception as exc:
        raise QuestCatalogError(
            f"could not read quest asset {path}: {exc}"
        ) from exc
    if isinstance(script, bytes):
        return script.decode("utf-8-sig")
    if isinstance(script, str):
        return script.lstrip("\ufeff")
    raise QuestCatalogError(f"quest asset {path} is not text")


def load_quest_catalog(game_dir: Path) -> QuestCatalog:
    """Load all installed quest definitions from ResourcesBundle."""
    from .inventory import (
        RESOURCE_BUNDLE_PATH,
        _unitypy_module,
    )

    bundle_path = (
        game_dir.expanduser().absolute() / RESOURCE_BUNDLE_PATH
    )
    if not bundle_path.is_file():
        raise QuestCatalogError(f"ResourcesBundle not found: {bundle_path}")
    UnityPy = _unitypy_module()
    try:
        environment = UnityPy.load(str(bundle_path))
    except Exception as exc:
        raise QuestCatalogError(f"could not open ResourcesBundle: {exc}") from exc
    container = getattr(environment, "container", None)
    items = getattr(container, "items", None)
    if not callable(items):
        raise QuestCatalogError(
            "ResourcesBundle has no readable asset container"
        )

    assets: dict[str, str] = {}
    warnings: list[str] = []
    for path, pointer in items():
        if not isinstance(path, str):
            continue
        if npc.QUEST_JSON_MARKER not in path.casefold():
            continue
        try:
            assets[path] = _text_asset_script(pointer, path)
        except QuestCatalogError as exc:
            warnings.append(str(exc))
    catalog = build_quest_catalog(assets)
    return QuestCatalog(
        quests=catalog.quests,
        warnings=tuple((*warnings, *catalog.warnings)),
    )


def _negative_signature(key: str) -> Optional[tuple[str, ...]]:
    tokens = npc.CAMEL_TOKEN_PATTERN.findall(key)
    changed = False
    normalized: list[str] = []
    for token in tokens:
        lowered = token.casefold()
        if lowered.startswith("ne") and len(token) > 3:
            normalized.append(token[2:].casefold())
            changed = True
        else:
            normalized.append(lowered)
    return tuple(normalized) if changed else None


def _has_conflicting_flags(enabled_flags: Sequence[str]) -> bool:
    enabled = set(enabled_flags)
    signatures: dict[tuple[str, ...], set[str]] = {}
    for key in enabled:
        signature = _negative_signature(key)
        if signature is None:
            continue
        signatures.setdefault(signature, set()).add(key)
    for signature, negative_keys in signatures.items():
        positive = "".join(signature)
        for key in enabled:
            if key not in negative_keys and key.casefold() == positive:
                return True
    return False


def correlate_quest(
    definition: QuestDefinition,
    flags: npc.NpcFlagTable,
) -> QuestState:
    values = MappingProxyType({record.key: record.value for record in flags.records})
    referenced_keys = tuple(
        sorted({reference.key for reference in definition.references})
    )
    known_flags = tuple(key for key in referenced_keys if key in values)
    missing_flags = tuple(key for key in referenced_keys if key not in values)
    enabled_flags = tuple(key for key in known_flags if values[key] == 1)
    disabled_flags = tuple(key for key in known_flags if values[key] == 0)

    if not known_flags:
        status = QUEST_STATUS_NO_FLAGS
        reason = "No referenced quest flags are present in this save schema."
    elif _has_conflicting_flags(enabled_flags):
        status = QUEST_STATUS_CONFLICTING
        reason = (
            "Mutually exclusive-looking flag variants are enabled; "
            "inspect the related flags before drawing conclusions."
        )
    elif not enabled_flags:
        status = QUEST_STATUS_NOT_STARTED
        reason = "None of the referenced save flags are enabled."
    elif len(enabled_flags) == len(known_flags):
        status = QUEST_STATUS_COMPLETED
        reason = "Every referenced save flag is enabled."
    else:
        status = QUEST_STATUS_IN_PROGRESS
        reason = (
            f"{len(enabled_flags)} of {len(known_flags)} referenced "
            "save flags are enabled."
        )

    return QuestState(
        definition=definition,
        progress=QuestProgress(
            status=status,
            known_flags=known_flags,
            enabled_flags=enabled_flags,
            disabled_flags=disabled_flags,
            missing_flags=missing_flags,
            reason=reason,
        ),
    )


def correlate_quests(
    catalog: QuestCatalog,
    flags: npc.NpcFlagTable,
) -> tuple[QuestState, ...]:
    """Correlate every quest definition with one save's NPC flag table."""
    return tuple(correlate_quest(quest, flags) for quest in catalog.quests)


def quest_search_text(state: QuestState) -> str:
    """Return lower-level searchable text for one quest state."""
    definition = state.definition
    progress = state.progress
    return " ".join(
        (
            definition.title,
            definition.asset_name,
            definition.asset_path,
            definition.type_label,
            progress.status,
            " ".join(definition.associations),
            " ".join(progress.known_flags),
            " ".join(progress.missing_flags),
            " ".join(definition.evidence),
        )
    ).casefold()


def quest_flag_evidence(
    definition: QuestDefinition,
) -> Mapping[str, tuple[QuestFlagReference, ...]]:
    grouped: dict[str, list[QuestFlagReference]] = {}
    for reference in definition.references:
        grouped.setdefault(reference.key, []).append(reference)
    return MappingProxyType(
        {
            key: tuple(references)
            for key, references in grouped.items()
        }
    )
