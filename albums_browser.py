"""
albums_browser.py - the Albums tree (Artist > Album > songs) used by the Albums tab and the skinned Albums window.

Moved out of music_player.py so the default view and the skinned mode can each have one. It reads the player's
library_index and calls back into the player (play_album, play_artist, queue_album, queue_track, _album_action) to
play things; status(text) shows a line of text wherever the owner wants it.
"""

import tkinter as tk
from tkinter import ttk

import library_index

ALBUMS_SEARCH_OPEN_CAP = 50   # Albums search: at most this many albums open with their matching songs
TYPE_JUMP_RESET_MS = 800      # type-to-jump: typing pause that starts a new prefix


class AlbumsBrowser:

    def __init__(self, player, frame, status=None, get_query=None):
        self.player = player
        self._status = status or (lambda text: None)
        self._get_query = get_query or (lambda: '')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=('Info',), show='tree headings', height=5, selectmode='browse')
        tree.heading('#0', text='Artist / Album / Song')
        tree.heading('Info', text='Info')
        tree.column('#0', width=380)
        tree.column('Info', width=130, stretch=False, anchor=tk.E)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.albums_tree = tree
        self._albums_rows = {}         # iid -> ('artist', key) | ('album', key) | ('song', album_key, track)
        self._albums_artist_iids = {}  # artist key -> iid
        self._albums_pending = {}      # album iid -> album key, songs not inserted yet
        self._albums_query = None      # search text the tree shows (None: rebuild on next filter)
        self._type_jump = ''
        self._type_jump_job = None
        self.albums_menu = tk.Menu(self.player.root, tearoff=0)
        tree.bind('<<TreeviewOpen>>', self._on_album_open)
        tree.bind('<Double-1>', self._on_albums_double_click)
        tree.bind('<Return>', lambda e: self._play_albums_row(self.albums_tree.focus()) or 'break')
        tree.bind('<Button-3>', self._on_albums_right_click)
        tree.bind('<Control-Button-1>', self._on_albums_right_click)
        tree.bind('<Key>', self._on_albums_key)
        self.refresh()

    def refresh(self):
        """The library index changed: rebuild the tree, keeping the search text (quietly when there is none)."""
        self._albums_query = None
        query = (self._get_query() or '').strip()
        if query:
            self.filter(query)
        else:
            self._fill_albums_tree(None)
            self._albums_query = ''

    def filter(self, query):
        q = (query or '').strip()
        if q == self._albums_query:
            return
        self._albums_query = q
        idx = self.player.library_index
        if not q or idx is None:
            self._fill_albums_tree(None)
            if idx is not None:
                self._status(f"{len(idx.artists)} artists")
            return
        hits = idx.search(q)
        self._fill_albums_tree(hits)
        songs = sum(len(h.tracks) for h in hits if not h.whole_album)
        self._status(f"{len(hits)} albums match" + (f" ({songs} songs)" if songs else ""))

    def _fill_albums_tree(self, hits):
        """hits None: every artist, folded. Else the search hits: their artists open, albums with song-level
        matches open showing those songs (the first ALBUMS_SEARCH_OPEN_CAP), other albums folded."""
        tree = self.albums_tree
        tree.delete(*tree.get_children())
        self._albums_rows.clear()
        self._albums_artist_iids.clear()
        self._albums_pending.clear()
        idx = self.player.library_index
        if idx is None:
            return
        if hits is None:
            for artist in idx.artists:
                a_iid = self._insert_artist_row(artist, open_=False)
                for album in artist.albums:
                    self._insert_album_row(a_iid, album)
            return
        opened = 0
        for hit in hits:
            artist = idx.artist(hit.album.artist_key)
            if artist is None:
                continue
            a_iid = self._albums_artist_iids.get(artist.key) or self._insert_artist_row(artist, open_=True)
            if not hit.whole_album and opened < ALBUMS_SEARCH_OPEN_CAP:
                self._insert_album_row(a_iid, hit.album, songs=hit.tracks)
                opened += 1
            else:
                self._insert_album_row(a_iid, hit.album)

    def _insert_artist_row(self, artist, open_):
        n = len(artist.albums)
        iid = self.albums_tree.insert('', 'end', text=artist.name, open=open_,
                                      values=(f"{n} album{'s' if n != 1 else ''}",))
        self._albums_rows[iid] = ('artist', artist.key)
        self._albums_artist_iids[artist.key] = iid
        return iid

    def _insert_album_row(self, parent, album, songs=None):
        """songs None: fold the album and load its songs when it is first opened."""
        n = len(album.tracks)
        info = f"{n} song{'s' if n != 1 else ''}"
        if album.year:
            info = f"{album.year} · {info}"
        iid = self.albums_tree.insert(parent, 'end', text=album.name, values=(info,), open=songs is not None)
        self._albums_rows[iid] = ('album', album.key)
        if songs is None:
            self.albums_tree.insert(iid, 'end', text='…')   # placeholder so the row can fold open
            self._albums_pending[iid] = album.key
        else:
            self._insert_song_rows(iid, album, songs)
        return iid

    def _insert_song_rows(self, parent, album, songs):
        for t in songs:
            key = library_index.song_sort_key(t)
            num = '' if key[0] else (f"{key[1]}-{key[2]:02}" if key[1] > 1 else str(key[2]))
            dur = t.format_duration() if hasattr(t, 'format_duration') else ''
            iid = self.albums_tree.insert(parent, 'end', text=f"{num:>4}   {t.title}", values=(dur,))
            self._albums_rows[iid] = ('song', album.key, t)

    def _on_album_open(self, event):
        iid = self.albums_tree.focus()
        album_key = self._albums_pending.pop(iid, None)
        if album_key is None or self.player.library_index is None:
            return
        album = self.player.library_index.album(album_key)
        self.albums_tree.delete(*self.albums_tree.get_children(iid))
        if album is not None:
            self._insert_song_rows(iid, album, album.tracks)

    def _play_albums_row(self, iid):
        row = self._albums_rows.get(iid) if iid else None
        if row is None:
            return
        if row[0] == 'artist':
            self.player.play_artist(row[1])
        elif row[0] == 'album':
            self.player.play_album(row[1])
        else:
            self.player.play_album(row[1], start_track=row[2])

    def _on_albums_double_click(self, event):
        iid = self.albums_tree.identify_row(event.y)
        if not iid or self.albums_tree.identify_element(event.x, event.y).endswith('indicator'):
            return None   # the fold arrow folds, as usual
        self._play_albums_row(iid)
        return 'break'    # play without also folding the row

    def _on_albums_right_click(self, event):
        tree = self.albums_tree
        iid = tree.identify_row(event.y)
        row = self._albums_rows.get(iid) if iid else None
        idx = self.player.library_index
        if row is None or idx is None:
            return
        tree.selection_set(iid)
        tree.focus(iid)
        menu = self.albums_menu
        menu.delete(0, tk.END)
        if row[0] == 'artist':
            menu.add_command(label="Play artist", command=lambda k=row[1]: self.player.play_artist(k))
        else:
            album = idx.album(row[1])
            if album is None:
                return
            if row[0] == 'song':
                track = row[2]
                menu.add_command(label="Play Next", command=lambda: self.player.queue_track(track, play_next=True))
                menu.add_command(label="Add to Up Next", command=lambda: self.player.queue_track(track))
                menu.add_separator()
            menu.add_command(label="Play album from start", command=lambda: self.player._album_action(album, 'album'))
            menu.add_command(label="Play artist", command=lambda: self.player._album_action(album, 'artist'))
            menu.add_command(label="Add album to Up Next", command=lambda: self.player._album_action(album, 'queue'))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except (RuntimeError, tk.TclError):
                pass

    def _albums_artist_key_at(self, iid):
        tree = self.albums_tree
        while iid and tree.parent(iid):
            iid = tree.parent(iid)
        row = self._albums_rows.get(iid) if iid else None
        return row[1] if row and row[0] == 'artist' else None

    def _on_albums_key(self, event):
        """Type-to-jump: typed letters jump to the next artist whose name starts with them."""
        ctrl, alt = bool(event.state & 0x4), bool(event.state & 0x20000)
        if ctrl != alt:   # Ctrl or Alt alone: leave shortcuts alone (both = AltGr, a typed letter)
            return None
        ch = event.char
        if not ch or not ch.isprintable() or (ch == ' ' and not self._type_jump):
            return None
        idx = self.player.library_index
        if idx is None or not idx.artists:
            return 'break'
        if self._type_jump_job is not None:
            self.player.root.after_cancel(self._type_jump_job)
        self._type_jump += ch
        self._type_jump_job = self.player.root.after(TYPE_JUMP_RESET_MS, self._reset_type_jump)
        from_key = self._albums_artist_key_at(self.albums_tree.focus())
        if from_key is not None and len(self._type_jump) == 1:
            keys = [a.key for a in idx.artists]   # a fresh letter starts after the current artist
            pos = keys.index(from_key) if from_key in keys else -1
            from_key = keys[(pos + 1) % len(keys)]
        artist = idx.find_artist(self._type_jump, from_key=from_key)
        iid = self._albums_artist_iids.get(artist.key) if artist is not None else None
        if iid:
            self.albums_tree.selection_set(iid)
            self.albums_tree.focus(iid)
            self.albums_tree.see(iid)
        return 'break'

    def _reset_type_jump(self):
        self._type_jump = ''
        self._type_jump_job = None
