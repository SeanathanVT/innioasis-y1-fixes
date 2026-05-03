# DEX Analysis

Reference for the smali-level patches in `src/patches/patch_y1_apk.py` (Y1 music player APK, Artist→Album navigation). Captured during DEX-level inspection of `com.innioasis.y1_3.0.2.apk` via androguard.

## ArtistsActivity.confirm()

```
registers_size=5; p0=this=v4
Artist-tap branch: instructions 53-79 (isShowArtists==true, isMultiSelect==false)
switchSongSortType() call: instructions 72-73 (replaced with Intent launch)
Selected artist stored in: ArtistsActivity.artist (Ljava/lang/String;)
```

Patch A intercepts the artist-tap branch and launches `AlbumsActivity` with the selected artist passed via the `"artist_key"` Intent extra, replacing the direct song-list navigation that the stock player does.

## AlbumsActivity.initView()

```
registers_size=3; p0=this=v2; locals=2 (patched to 8)
UI Resource ID: 2131820833 (0x7f110121)
getAlbumListBySort() launches async coroutine (safe to bypass with early return)
```

Patch B reads the `"artist_key"` Intent extra, calls `SongDao.getSongsByArtistSortByAlbum()` to fetch the artist's albums sorted by title, deduplicates them, and displays the album list with cover art before drilling down to songs. Falls back to the standard album-list view if no artist is specified.

## Song Database Query

```sql
SELECT * FROM song
WHERE isAudiobook = 0 AND artist = ?
ORDER BY lower(pinyinAlbum)
```

Song data accessed via `SongDao.getSongsByArtistSortByAlbum(String)`. Patch C makes the `songDao` field public (required for DEX bytecode access — bypasses Kotlin compiler-generated accessors which fail on older Dalvik VMs / API 17).
