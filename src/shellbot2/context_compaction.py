"""
Context compaction utilities for model message history.

This module operates on interaction objects and returns a compacted COPY of
their message payloads, preserving message JSON structure.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any
import logging

logger = logging.getLogger(__name__)

@dataclass
class ContextCompactionConfig:
    burden_threshold: float = 80000.0
    base_weight: float = 1.0
    weight_growth: float = 0.35
    interior_min_length: int = 700
    final_min_length: int = 3500
    preserve_head_chars: int = 240
    preserve_tail_chars: int = 240
    truncation_marker: str = "\n\n... message truncated ...\n\n"
    max_total_length: int = 60000

    @classmethod
    def from_dict(cls, conf: dict[str, Any]) -> "ContextCompactionConfig":
        return cls(
            burden_threshold=float(conf.get("burden_threshold", cls.burden_threshold)),
            base_weight=float(conf.get("base_weight", cls.base_weight)),
            weight_growth=float(conf.get("weight_growth", cls.weight_growth)),
            interior_min_length=int(conf.get("interior_min_length", cls.interior_min_length)),
            final_min_length=int(conf.get("final_min_length", cls.final_min_length)),
            preserve_head_chars=int(conf.get("preserve_head_chars", cls.preserve_head_chars)),
            preserve_tail_chars=int(conf.get("preserve_tail_chars", cls.preserve_tail_chars)),
            truncation_marker=str(conf.get("truncation_marker", cls.truncation_marker)),
            max_total_length=int(conf.get("max_total_length", cls.max_total_length)),
        )


def _message_has_user_prompt(message: dict[str, Any]) -> bool:
    parts = message.get("parts", [])
    if not isinstance(parts, list):
        return False
    return any(
        isinstance(part, dict) and part.get("part_kind") == "user-prompt"
        for part in parts
    )


def _truncate_text(text: str, head_chars: int, tail_chars: int, marker: str) -> str:
    if not text:
        return text
    min_visible = max(0, head_chars) + max(0, tail_chars)
    if len(text) <= min_visible + len(marker):
        return text
    return f"{text[:head_chars]}{marker}{text[-tail_chars:]}"


def _message_text_length(message: dict[str, Any]) -> int:
    """Estimate message length by summing textual content fields."""
    total = 0
    parts = message.get("parts", [])
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict):
                content = part.get("content")
                if isinstance(content, str):
                    total += len(content)
    content = message.get("content")
    if isinstance(content, str):
        total += len(content)
    return total


def _truncate_message_content_fields(
    message: dict[str, Any],
    head_chars: int,
    tail_chars: int,
    marker: str,
) -> None:
    """Truncate textual `content` fields in-place while preserving JSON schema."""
    parts = message.get("parts", [])
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            content = part.get("content")
            if isinstance(content, str):
                part["content"] = _truncate_text(
                    content,
                    head_chars=head_chars,
                    tail_chars=tail_chars,
                    marker=marker,
                )

    content = message.get("content")
    if isinstance(content, str):
        message["content"] = _truncate_text(
            content,
            head_chars=head_chars,
            tail_chars=tail_chars,
            marker=marker,
        )


def _dynamic_cutoff(interior_min_length: int, burden_ratio: float) -> int:
    """
    Compute the adaptive cutoff for truncating interior messages.

    Higher burden_ratio lowers the cutoff and increases truncation aggressiveness.
    """
    ratio_for_cutoff = max(0.25, burden_ratio)
    cutoff = int(interior_min_length / ratio_for_cutoff)
    cutoff = max(interior_min_length // 2, cutoff)
    cutoff = min(interior_min_length * 4, cutoff)
    return cutoff


def _compact_interaction_messages(
    messages: list[dict[str, Any]],
    burden_ratio: float,
    config: ContextCompactionConfig,
) -> None:
    if len(messages) >= 3:
        for i in range(1, len(messages) - 1):
            candidate = messages[i]
            if _message_has_user_prompt(candidate):
                continue

            msg_len = _message_text_length(candidate)
            length_cutoff = _dynamic_cutoff(config.interior_min_length, burden_ratio)
            if msg_len >= length_cutoff:
                _truncate_message_content_fields(
                    candidate,
                    head_chars=config.preserve_head_chars,
                    tail_chars=config.preserve_tail_chars,
                    marker=config.truncation_marker,
                )

    last_message = messages[-1] if messages else None
    if isinstance(last_message, dict):
        if not _message_has_user_prompt(last_message):
            last_len = _message_text_length(last_message)
            if last_len >= config.final_min_length and burden_ratio >= 1.0:
                _truncate_message_content_fields(
                    last_message,
                    head_chars=config.preserve_head_chars,
                    tail_chars=config.preserve_tail_chars,
                    marker=config.truncation_marker,
                )


def compact_recent_interactions(
    interactions: list[Any],
    config: ContextCompactionConfig,
) -> list[dict[str, Any]]:
    """
    Build a compacted COPY of interaction messages for model context.

    Iterates from most recent interaction backward, accumulating:
    burden += weight * interaction_length
    where weight grows with age (older interactions).

    Stops once compacted payload size exceeds `max_total_length`, checked after
    each interaction so at least one interaction is returned when available.
    """
    if not interactions:
        return []

    threshold = max(1.0, config.burden_threshold)
    burden = 0.0
    total_length = 0
    compacted_recent_to_oldest: list[list[dict[str, Any]]] = []
    orig_total_length = sum(
        _message_text_length(msg.message)
        for interaction in interactions
        for msg in interaction.messages
    )
    logger.info(f"Compacting {len(interactions)} interactions, original total length: {orig_total_length}")
    for reverse_idx, interaction in enumerate(reversed(interactions)):
        messages = [deepcopy(msg.message) for msg in interaction.messages]
        weight = config.base_weight + (config.weight_growth * reverse_idx)
        interaction_len = sum(_message_text_length(msg) for msg in messages)
        burden += max(0.0, weight) * interaction_len
        burden_ratio = burden / threshold

        _compact_interaction_messages(
            messages=messages,
            burden_ratio=burden_ratio,
            config=config,
        )

        compacted_recent_to_oldest.append(messages)
        total_length += sum(_message_text_length(msg) for msg in messages)

        # Check after compacting each interaction, but always keep at least one
        # interaction when history exists.
        if len(compacted_recent_to_oldest) >= 1 and total_length > config.max_total_length:
            break

    logger.info(f"Compacted {len(compacted_recent_to_oldest)} interactions, compacted total length: {total_length}")
    compacted_oldest_to_recent = reversed(compacted_recent_to_oldest)
    return [msg for interaction_msgs in compacted_oldest_to_recent for msg in interaction_msgs]
