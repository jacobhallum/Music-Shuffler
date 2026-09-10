"""
tests/test_playlist_queue.py - stdlib-only unit tests for playlist_queue.py

Run with:
    python -m unittest tests/test_playlist_queue.py -v
"""

import os
import sys
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from playlist_queue import queue_target, advance, previous_position


class QueueTargetTests(unittest.TestCase):
    """queue_target computes (remove_index, insert_index, new_current_index,
    new_queued_count) for moving a track into the queued block right after
    current_index."""

    def test_add_to_up_next_stacks_in_add_order(self):
        # current_index=0, nothing queued yet. Add track currently at index 5.
        remove_idx, insert_idx, cur, count = queue_target(
            current_index=0, queued_count=0, play_next=False, existing_index=5
        )
        self.assertEqual(remove_idx, 5)
        self.assertEqual(insert_idx, 1)  # after pop, slot 1 is right after current
        self.assertEqual(cur, 0)
        self.assertEqual(count, 1)

        # Add a second up-next track (already-queued count=1), currently at index 6.
        remove_idx, insert_idx, cur, count = queue_target(
            current_index=0, queued_count=1, play_next=False, existing_index=6
        )
        self.assertEqual(remove_idx, 6)
        self.assertEqual(insert_idx, 2)  # after the first queued song
        self.assertEqual(cur, 0)
        self.assertEqual(count, 2)

    def test_play_next_lands_ahead_of_existing_up_next_block(self):
        # User's exact example: current song, 3 up-next songs queued already.
        # Play Next on a 4th track should land it at position 1 (front of block).
        remove_idx, insert_idx, cur, count = queue_target(
            current_index=0, queued_count=3, play_next=True, existing_index=10
        )
        self.assertEqual(remove_idx, 10)
        self.assertEqual(insert_idx, 1)
        self.assertEqual(cur, 0)
        self.assertEqual(count, 4)

    def test_requeue_track_already_in_queued_block_repositions_without_count_change(self):
        # current_index=0, queued_count=2 (positions 1,2 are queued).
        # Play Next on the track currently at position 2 (last in block) should
        # move it to position 1, count stays 2.
        remove_idx, insert_idx, cur, count = queue_target(
            current_index=0, queued_count=2, play_next=True, existing_index=2
        )
        self.assertEqual(remove_idx, 2)
        self.assertEqual(insert_idx, 1)
        self.assertEqual(cur, 0)
        self.assertEqual(count, 2)  # unchanged - track was already queued

    def test_queuing_track_before_current_index_shifts_current_index_down(self):
        # current_index=5, queued_count=0. Track to queue sits at index 2 (before current).
        remove_idx, insert_idx, cur, count = queue_target(
            current_index=5, queued_count=0, play_next=False, existing_index=2
        )
        self.assertEqual(remove_idx, 2)
        # Removing index 2 shifts current_index from 5 to 4; insert right after.
        self.assertEqual(cur, 4)
        self.assertEqual(insert_idx, 5)
        self.assertEqual(count, 1)

    def test_existing_index_none_falls_back_to_plain_insert(self):
        # Track not found in playlist (shouldn't normally happen, but handle it):
        # no pop, just insert at the queue-block boundary.
        remove_idx, insert_idx, cur, count = queue_target(
            current_index=0, queued_count=1, play_next=False, existing_index=None
        )
        self.assertIsNone(remove_idx)
        self.assertEqual(insert_idx, 2)
        self.assertEqual(cur, 0)
        self.assertEqual(count, 2)


class AdvanceTests(unittest.TestCase):
    """advance decides the next track: forward step, queue consumption,
    wrap, reshuffle-on-temp-view-end, and stop-on-empty."""

    def test_normal_forward_step_no_queue(self):
        action, new_index, count, temp = advance(
            current_index=2, length=10, queued_count=0, is_temp_view=False
        )
        self.assertEqual(action, 'play')
        self.assertEqual(new_index, 3)
        self.assertEqual(count, 0)
        self.assertFalse(temp)

    def test_forward_step_consumes_one_queued_item(self):
        action, new_index, count, temp = advance(
            current_index=0, length=10, queued_count=2, is_temp_view=False
        )
        self.assertEqual(action, 'play')
        self.assertEqual(new_index, 1)
        self.assertEqual(count, 1)  # decremented
        self.assertFalse(temp)

    def test_wrap_replays_when_reshuffle_disabled(self):
        # Truly Random / default: replay the same order from index 0.
        action, new_index, count, temp = advance(
            current_index=9, length=10, queued_count=0, is_temp_view=False
        )
        self.assertEqual((action, new_index, count, temp), ('play', 0, 0, False))

    def test_wrap_regenerates_when_reshuffle_enabled(self):
        # Smart Shuffle: regenerate a fresh shuffle at the cycle boundary.
        action, new_index, count, temp = advance(
            current_index=9, length=10, queued_count=0, is_temp_view=False,
            reshuffle_on_wrap=True,
        )
        self.assertEqual(action, 'reshuffle')
        self.assertEqual(new_index, 0)
        self.assertEqual(count, 0)

    def test_reshuffle_flag_does_not_affect_mid_playlist_advance(self):
        action, new_index, count, temp = advance(
            current_index=3, length=10, queued_count=0, is_temp_view=False,
            reshuffle_on_wrap=True,
        )
        self.assertEqual((action, new_index), ('play', 4))

    def test_temp_view_still_reshuffles_without_flag(self):
        action, *_ = advance(
            current_index=4, length=5, queued_count=0, is_temp_view=True
        )
        self.assertEqual(action, 'reshuffle')

    def test_reshuffle_on_temp_view_end(self):
        action, new_index, count, temp = advance(
            current_index=4, length=5, queued_count=0, is_temp_view=True
        )
        self.assertEqual(action, 'reshuffle')
        self.assertEqual(new_index, 0)
        self.assertEqual(count, 0)
        self.assertFalse(temp)

    def test_stop_on_empty_playlist(self):
        action, new_index, count, temp = advance(
            current_index=0, length=0, queued_count=0, is_temp_view=False
        )
        self.assertEqual(action, 'stop')


class PreviousPositionTests(unittest.TestCase):

    def test_wraps_to_end_from_zero(self):
        self.assertEqual(previous_position(0, 10), 9)

    def test_steps_back_normally(self):
        self.assertEqual(previous_position(5, 10), 4)


if __name__ == '__main__':
    unittest.main()
