"""Match a spoken utterance against the legal moves of the current position.

The position is the dictionary: every legal move is expanded into the voice
forms a player may plausibly use for it, and the utterance is looked up among
them. Several moves sharing a form is a normal outcome — it is reported as
`ambiguous` with every candidate, never silently narrowed to the first one.
"""

from __future__ import annotations

from collections import defaultdict

import chess

from yura_chess.voice.types import (
    MoveResolution,
    Normalized,
    RecognizedMove,
    ResolutionStatus,
    Signature,
    Token,
    TokenKind,
)

# Confidence tiers, monotonic in how completely the utterance pinned the move down.
_FULL_COORDINATES = 1.0
_PIECE_PARTIAL_SOURCE = 0.9
_PIECE_DESTINATION = 0.85
_DESTINATION_ONLY = 0.75
_UNKNOWN_WORD_PENALTY = 0.05
_MAX_UNKNOWN_PENALTY = 0.15


def resolve(normalized: Normalized, board: chess.Board) -> MoveResolution:
    """Resolve one utterance against `board`; never mutates the board."""
    recognized = recognize(normalized.signature)
    if not normalized.signature:
        return MoveResolution(ResolutionStatus.UNMATCHED, recognized=recognized)

    forms: defaultdict[Signature, list[str]] = defaultdict(list)
    tiers: dict[Signature, float] = {}
    for legal_move in board.legal_moves:
        for form, tier in _voice_forms(board, legal_move):
            uci = legal_move.uci()
            if uci not in forms[form]:
                forms[form].append(uci)
            tiers[form] = max(tiers.get(form, 0.0), tier)

    matches = forms.get(normalized.signature)
    matched_signature = normalized.signature
    if not matches:
        focused: dict[str, tuple[Signature, float]] = {}
        for signature in _focused_signatures(normalized.signature):
            for move_uci in forms.get(signature, ()):
                tier = tiers[signature]
                previous = focused.get(move_uci)
                if previous is None or tier > previous[1]:
                    focused[move_uci] = (signature, tier)
        if not focused:
            return MoveResolution(ResolutionStatus.UNMATCHED, recognized=recognized)
        if len(focused) > 1:
            return MoveResolution(
                ResolutionStatus.AMBIGUOUS,
                candidates=tuple(focused),
                recognized=recognized,
            )
        move_uci, (matched_signature, _) = next(iter(focused.items()))
        matches = [move_uci]
        recognized = recognize(matched_signature)
    if len(matches) > 1:
        return MoveResolution(ResolutionStatus.AMBIGUOUS, candidates=tuple(matches), recognized=recognized)

    confidence = tiers[matched_signature] - min(
        _UNKNOWN_WORD_PENALTY * len(normalized.unknown_words),
        _MAX_UNKNOWN_PENALTY,
    )
    return MoveResolution(
        ResolutionStatus.RESOLVED,
        confidence=max(confidence, 0.0),
        move=matches[0],
        candidates=(matches[0],),
        recognized=recognized,
    )


def recognize(signature: Signature) -> RecognizedMove:
    """Read the move the speaker described, independently of its legality.

    The last square named is the destination and an earlier one the source: that
    is the order a move is spoken in, and it survives an illegal move.
    """
    squares = [token.value for token in signature if token.kind is TokenKind.SQUARE]
    files = [token.value for token in signature if token.kind is TokenKind.FILE]
    ranks = [token.value for token in signature if token.kind is TokenKind.RANK]
    pieces = [token.value for token in signature if token.kind is TokenKind.PIECE]
    promotions = [token.value for token in signature if token.kind is TokenKind.PROMOTION]
    if len(squares) > 2:
        squares = []
    return RecognizedMove(
        piece=pieces[0] if len(set(pieces)) == 1 else None,
        source=squares[0] if len(squares) > 1 else None,
        source_file=files[0] if files else None,
        source_rank=ranks[0] if ranks else None,
        destination=squares[-1] if squares else None,
        promotion=promotions[0] if promotions else None,
        capture=any(token.kind is TokenKind.CAPTURE for token in signature),
        castle_short=any(token.kind is TokenKind.CASTLE_SHORT for token in signature),
        castle_long=any(token.kind is TokenKind.CASTLE_LONG for token in signature),
    )


def _focused_signatures(signature: Signature) -> tuple[Signature, ...]:
    """Move-shaped slices inside conversational speech, ordered as spoken.

    Every matching slice is considered. If two different legal moves were
    actually named, `resolve` returns ambiguity instead of choosing the last one.
    """
    square_values = [token.value for token in signature if token.kind is TokenKind.SQUARE]
    if len(square_values) == 3 and square_values[0] != square_values[-1]:
        return ()

    focused: list[Signature] = []
    for start in range(len(signature)):
        for end in range(start + 2, min(len(signature), start + 5) + 1):
            candidate = signature[start:end]
            squares = sum(token.kind is TokenKind.SQUARE for token in candidate)
            pieces = sum(token.kind is TokenKind.PIECE for token in candidate)
            move_shaped = squares >= 2 if len(square_values) >= 2 else squares == 1 and pieces == 1
            if move_shaped:
                if candidate not in focused:
                    focused.append(candidate)
    return tuple(focused)


def _voice_forms(board: chess.Board, move: chess.Move) -> list[tuple[Signature, float]]:
    """Every signature that may denote `move`, with the confidence it earns."""
    if board.is_castling(move):
        kind = TokenKind.CASTLE_SHORT if board.is_kingside_castling(move) else TokenKind.CASTLE_LONG
        castle: list[tuple[Signature, float]] = [((Token(kind),), _FULL_COORDINATES)]
        # Castling is also legal to speak as a plain king move.
        return castle + _plain_forms(board, move)
    return _plain_forms(board, move)


def _plain_forms(board: chess.Board, move: chess.Move) -> list[tuple[Signature, float]]:
    piece = board.piece_at(move.from_square)
    if piece is None:  # pragma: no cover - a legal move always has a moving piece
        return []
    letter = piece.symbol().upper()
    source = chess.square_name(move.from_square)
    destination = chess.square_name(move.to_square)

    heads: list[tuple[tuple[Token, ...], float]] = [
        ((Token(TokenKind.PIECE, letter), Token(TokenKind.SQUARE, source)), _FULL_COORDINATES),
        ((Token(TokenKind.SQUARE, source),), _FULL_COORDINATES),
        ((Token(TokenKind.PIECE, letter), Token(TokenKind.FILE, source[0])), _PIECE_PARTIAL_SOURCE),
        ((Token(TokenKind.PIECE, letter), Token(TokenKind.RANK, source[1])), _PIECE_PARTIAL_SOURCE),
        ((Token(TokenKind.PIECE, letter),), _PIECE_DESTINATION),
        ((), _DESTINATION_ONLY),
    ]
    # Naming the capture is optional, but claiming one where there is none is not.
    middles: list[tuple[Token, ...]] = [(), (Token(TokenKind.CAPTURE),)] if board.is_capture(move) else [()]
    # The promotion piece is never assumed: the form without it is shared by all
    # four promotions of one pawn, so leaving it unsaid stays ambiguous.
    tails: list[tuple[Token, ...]] = [()]
    if move.promotion:
        tails.append((Token(TokenKind.PROMOTION, chess.piece_symbol(move.promotion) or ""),))

    forms: list[tuple[Signature, float]] = []
    for head, tier in heads:
        for middle in middles:
            for tail in tails:
                forms.append(((*head, *middle, Token(TokenKind.SQUARE, destination), *tail), tier))
    return forms
