"""
playlist_queue.py - pure, stdlib-only helpers for queue insertion and
playback-advance decisions used by music_player.py's current_playlist model.

No GUI / audio / third-party dependencies, so this can be unit tested in
isolation (see tests/test_playlist_queue.py).

Model recap (see music_player.py):
    current_playlist is the single source of truth - the full library in
    shuffle order (or a taken-over search-result order). A "queued block" of
    manually-queued tracks sits immediately after current_index; its size is
    tracked by the caller as queued_count. Queuing a track *moves* it (it
    already exists somewhere in current_playlist) rather than duplicating it.
"""


def queue_target(current_index, queued_count, play_next, existing_index):
    """Compute list ops to (re)position a track into the queued block that
    sits immediately after current_index.

    existing_index: the track's current index in the playlist, or None if
        it isn't present.

    Returns (remove_index, insert_index, new_current_index, new_queued_count).

    Caller applies the result as:
        if remove_index is not None:
            playlist.pop(remove_index)
        playlist.insert(insert_index, track)
    insert_index is expressed relative to the list AFTER the pop (if any).

    Play Next -> front of the queued block (position current_index + 1).
    Add to Up Next -> end of the queued block (position
        current_index + 1 + queued_count).

    If the track is already within the queued block (i.e. existing_index is
    one of the queued positions), it is repositioned and queued_count is
    unchanged. Otherwise queued_count grows by one. If existing_index is
    before current_index, removing it shifts current_index down by one.
    """
    new_current_index = current_index
    already_queued = (
        existing_index is not None
        and current_index < existing_index <= current_index + queued_count
    )

    if existing_index is not None and existing_index < current_index:
        new_current_index = current_index - 1

    if play_next:
        insert_index = new_current_index + 1
    elif already_queued:
        insert_index = new_current_index + queued_count  # last queued slot
    else:
        insert_index = new_current_index + 1 + queued_count

    new_queued_count = queued_count if already_queued else queued_count + 1

    return existing_index, insert_index, new_current_index, new_queued_count


def advance(current_index, length, queued_count, is_temp_view, *,
            reshuffle_on_wrap=False):
    """Decide the next track.

    Returns (action, new_index, new_queued_count, new_is_temp_view).
    action is one of:
        'play'      - play new_index in the current playlist
        'reshuffle' - regenerate a full shuffle and start at index 0.  Happens
                      when a taken-over search view ends, or (with
                      reshuffle_on_wrap=True, i.e. Smart Shuffle) at a normal
                      end-of-playlist wrap instead of replaying the same order.
        'stop'      - nothing to play (empty playlist)
    """
    if length == 0:
        return 'stop', current_index, queued_count, is_temp_view

    next_index = current_index + 1

    if next_index >= length:
        if is_temp_view:
            return 'reshuffle', 0, 0, False
        if reshuffle_on_wrap:
            return 'reshuffle', 0, 0, False
        return 'play', 0, 0, False

    new_queued_count = queued_count - 1 if queued_count > 0 else 0
    return 'play', next_index, new_queued_count, is_temp_view


def previous_position(current_index, length):
    """Modulo-wrap previous index (today's behavior)."""
    if length == 0:
        return current_index
    return (current_index - 1) % length
