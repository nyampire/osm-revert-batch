# osm-revert-batch — 設計

- **日付**: 2026-07-04
- **著者**: nyampire (with Claude)
- **目的**: `simple_revert` の CLI では扱いにくい「複数 changeset を順に revert し、
  失敗したものを把握して再実行できる」バッチ処理を Python から実現する

## 背景

[Zverik/simple-revert](https://github.com/Zverik/simple-revert) は OSM の
「単純な」changeset を revert する CLI ツール。ただし以下の弱点がある:

- **エラー処理が弱い**: upload 段の 412 Precondition Failed 検出後に対応処理が
  `pass` で書かれており、失敗が exit code に反映されない
- **複数 changeset のバッチ処理向けに作られていない**: CLI 引数として複数 ID を
  渡せるが、失敗時の挙動が「途中で例外を投げて終わる」しかない
- **OAuth が毎回走る**: `simple_revert` を shell から呼ぶと changeset ごとに
  ブラウザで認可が必要になる

本ツールは `simple_revert` を **ライブラリとして import** し、上記の弱点を
Python 側で塞ぐバッチ処理レイヤーを提供する。

## スコープ

### やること

- テキストファイルに列挙された changeset ID を上から順に revert
- 1 プロセス内で OAuth を 1 回だけ実施（`read_auth()` の結果を持ち回る）
- 失敗しても後続を続行し、最後に失敗一覧を出力
- 失敗した changeset は入力と同形式の `<input>.failed.txt` に書き出し、
  そのまま再実行の入力にできる
- `--dry-run` で OAuth 抜き、changeset ごとの `.osc` ファイル出力のみ
- 一時的エラー (HTTP 429 / 5xx / ネット断) は自動リトライ、雪崩は circuit
  breaker で中断、未処理の changeset は `<input>.remaining.txt` に保存して
  中断地点から再開できる

### やらないこと (YAGNI)

- リトライ / バックオフ
- 並列処理
- OAuth token のプロセス跨ぎ永続化（毎回ブラウザ認可でよい）
- JSON Lines ログ、進捗バー等の付加機能
- OSM 実 API E2E テスト（副作用が大きすぎるため単体テストのみ）
- `restore_version` 側のラップ（本ツールは `simple_revert` の changeset revert 専用）

## 配置

新規リポジトリ `~/git/osm-revert-batch/`。

```
osm-revert-batch/
├── pyproject.toml           # simple_revert に依存
├── README.md
├── docs/
│   └── design.md            # 本書
├── src/osm_revert_batch/
│   ├── __init__.py
│   ├── cli.py               # argparse + entrypoint
│   └── batch.py             # 中核ロジック
└── tests/
    ├── test_parser.py
    ├── test_failure_writer.py
    └── test_error_classify.py
```

## 依存

- Python 3.12+ (3.13 でも動作)
  - 3.11 で入った PEP 654 (exception groups) / PEP 657 (traceback 位置情報)、
    3.12 で入った f-string の入れ子改善・型パラメータ構文 (PEP 695) を活用
  - spec 内で `list[tuple[int, int]]` 等の PEP 585 記法や `X | None` 型構文を
    使うので、下限を 3.10 以上に置く必要がある
- `simple_revert >= 1.2.0` (PyPI から取得)
- 標準ライブラリのみで他は済ませる

## CLI

```
osm-revert-batch <input_file> [--comment "..."] [--dry-run] [--out-dir DIR]
```

| 引数 / オプション | 意味 |
|---|---|
| `<input_file>` | changeset ID を並べたテキストファイル |
| `--comment "..."` | 全 revert changeset 共通のコメント。省略時は `Reverting {id} by {user}` を自動生成 |
| `--dry-run` | OAuth せず、`.osc` を per-changeset で `--out-dir` に書き出す |
| `--out-dir DIR` | dry-run 時の出力先。デフォルト `./osc-out/` |

### 入力ファイル形式

プレーンテキスト、1 行 1 changeset ID。`#` 以降と空行は無視。

```
# 2024-11 の事故対応
12345
12346

# 別トピック
12348
```

## 実行フロー

```
1. parse input file → items = [(line_no, changeset_id), ...]
2. if not dry_run:
       auth = read_auth()          # ここで 1 回だけブラウザ認可
3. consecutive_transient = 0
   for i, (line_no, cs_id) in enumerate(items):
       try:
           # download と upload は with_retry() でラップ (下の "リトライ" 節参照)
           diffs, ch_users = with_retry(lambda: download_changesets(
               [cs_id], print_status))
           if not diffs:
               record success('no changes to revert')
               consecutive_transient = 0; continue
           changes = revert_changes(diffs, print_status)  # API 呼ばない、純計算
           if not changes:
               record success('no changes to upload')
               consecutive_transient = 0; continue

           if dry_run:
               path = out_dir / f'{cs_id}.osc'
               path.write_bytes(changes_to_osc(changes))
               record success(f'dry-run wrote {path}')
           else:
               tags = build_tags(cs_id, ch_users[cs_id], user_comment)
               with_retry(lambda: upload_changes_with_auth(auth, changes, tags))
               record success('uploaded')
           consecutive_transient = 0
       except RevertError as e:
           record failure(e.message)
           consecutive_transient = 0        # 本質的失敗はカウントしない
       except HTTPError as e:
           record failure(f'HTTP {e.code}: {e.message[:200]}')
           consecutive_transient = consecutive_transient + 1 if is_transient(e) else 0
       except (ConnectionError, Timeout) as e:
           record failure(f'Network: {e}')
           consecutive_transient += 1
       except Exception as e:
           record failure(f'Unexpected: {type(e).__name__}: {e}')
           consecutive_transient = 0

       if consecutive_transient >= CIRCUIT_BREAKER_THRESHOLD:
           remaining = [cs for _, cs in items[i+1:]]
           record circuit_break(reason='transient error threshold', remaining=remaining)
           break
   # 例外的パス: KeyboardInterrupt は try 外で catch し、未処理を remaining に

4. print summary to stderr (success / failure / remaining の 3 カテゴリ)
5. if failures:  write <input>.failed.txt
   if remaining: write <input>.remaining.txt
6. exit code:
     - 0: 全件成功 (差分なし含む)
     - 1: 走り切ったが 1 件以上失敗
     - 3: 中断 (circuit break or KeyboardInterrupt)、remaining あり
```

## リトライと circuit breaker

### 定数

| 名前 | 値 | 意味 |
|---|---|---|
| `MAX_RETRIES` | 3 | 1 件あたりの最大リトライ回数 |
| `BASE_BACKOFF_SECONDS` | 1.0 | exponential backoff の初期値 (1, 2, 4, 8 …) |
| `MAX_BACKOFF_SECONDS` | 60.0 | backoff の上限 |
| `CIRCUIT_BREAKER_THRESHOLD` | 5 | 連続 transient error 何件で中断するか |

まずは code 内の module-level 定数として書く。CLI flag への昇格は必要になってから。

### transient error の定義

- `HTTPError` のうち:
  - `code == 429` (Too Many Requests)
  - `500 <= code < 600` (サーバ側の一時障害)
- `requests.ConnectionError` / `requests.Timeout` / `requests.RequestException`
  のうち上記に含まれないもの (ネットワーク断・タイムアウト)

**含めないもの** (即失敗、リトライしない、circuit にもカウントしない):

- `HTTPError` で 4xx かつ 429 以外 (400 / 403 redaction / 404 等の永続的失敗)
- `RevertError` (redaction / structural change 等、本質的に revert 不能)

### `with_retry(fn: Callable) -> T`

```
attempts = 0
while True:
    try:
        return fn()
    except Exception as e:
        if not is_transient(e) or attempts >= MAX_RETRIES:
            raise
        wait = retry_after_header_or_backoff(e, attempts)
        wait = min(wait, MAX_BACKOFF_SECONDS)
        log to stderr: "transient error {short(e)}, retry in {wait}s"
        time.sleep(wait)
        attempts += 1
```

- HTTP 429 で `Retry-After` ヘッダが付いていればそれを使う。無ければ
  `BASE_BACKOFF_SECONDS * (2 ** attempts)` (= 1, 2, 4 秒)
- MAX_RETRIES を使い切っても transient なら例外を上位に投げ、上位ループが
  circuit breaker カウンタを進める

### circuit breaker

- 連続 `CIRCUIT_BREAKER_THRESHOLD` (=5) 件、transient error でリトライも
  尽きた changeset があれば中断
- 「成功」または「非 transient な失敗 (RevertError / 4xx 等)」を挟むと
  カウンタは 0 にリセット
- 中断時は `<input>.remaining.txt` に **その中断時点でまだループに入っていない
  changeset ID を** 同形式で書き出す。中断のトリガーになった changeset 自体は
  すでに `.failed.txt` に入っているので remaining には含めない
- `KeyboardInterrupt` (Ctrl+C) も circuit break と同じ経路: 現在処理中の
  changeset の後片付け (途中の changeset があれば close) をしたら
  remaining を書き出して exit code 3

### remaining.txt のフォーマット

```
# generated by osm-revert-batch on 2026-07-04 12:34:56
# aborted after 5 consecutive transient errors at line 42 (changeset 99999)
# 58 changesets remaining, resume by running:
#   osm-revert-batch changesets.txt.remaining.txt
99999
100000
100001
...
```

## 主要関数

### `parse_input(path: Path) -> list[tuple[int, int]]`

各行を読み、`#` 以降を切り、strip して空でなければ int にパース。
戻り値は `[(line_no, changeset_id), ...]`。
不正な行があれば `ValueError` を送出して `line_no` を含むメッセージを付ける。

### `build_tags(cs_id: int, user: str, user_comment: str | None) -> dict[str, str]`

```python
{
    'created_by': 'simple_revert.py',   # 本家互換（既存のレガシー監視等が拾える）
    'comment': user_comment or f'Reverting {cs_id} by {user}',
}
```

### `upload_changes_with_auth(auth, changes, tags) -> None`

本家 `simple_revert.common.upload_changes` を fork したもの。差分:

- `read_auth()` を呼ばず、外部から渡された `auth` を使う
- 412 Precondition Failed 含む upload 系 HTTP エラーは **例外として上位に伝播**
  させる（本家では pass や False で握り潰されて exit code に出ない）
- 具体的には `HTTPError` を再送出。412 は message からケースを判定して
  `RevertError('HTTP 412: Node ... is still used by ...')` に包み直す
- 成功時は None を返す
- changeset の close だけは best-effort（失敗しても例外にしない、1 時間で自動 close）

### `classify_error(exc: Exception) -> str`

失敗理由を短い文字列に正規化する:

| 型 | 出力 |
|---|---|
| `RevertError` | `e.message` そのまま |
| `HTTPError` | `HTTP {code}: {message[:200]}` |
| その他 | `Unexpected: {type(e).__name__}: {e}` |

### `write_failed_txt(path: Path, failures: list[tuple[int, str]])`

失敗した (changeset_id, reason) のリストを入力と同形式で書き出す:

```
# generated by osm-revert-batch on 2026-07-04 12:34:56
# input: changesets.txt (7 IDs, 5 succeeded, 2 failed)

# FAIL: Cannot revert redactions, see version 3 at https://openstreetmap.org/way/12345/history
12346

# FAIL: HTTP 412: Node 999 is still used by ways 5555,5556
12348
```

`# FAIL:` は通常のコメント (`#`) 扱いなので、そのまま再実行の入力にできる。

### `write_remaining_txt(path: Path, aborted_line_no: int, aborted_cs_id: int, remaining: list[int])`

circuit break 時に未処理の changeset ID を同形式で書き出す。ヘッダで中断
理由と再開コマンド例を残す（上の "remaining.txt のフォーマット" 参照）。

## エラー分類マトリクス

| 由来 | 発生タイミング | classify 出力 | transient? |
|---|---|---|---|
| redaction | `download_changesets` 中に HTTP 403 | `Cannot revert redactions, see version N at ...` | no |
| way/relation の member 変更 | `revert_changes` 中の `apply_diff` | `Members for way 12345 were changed, cannot roll that back` | no |
| changeset そのものの download 失敗 (404 等) | `download_changesets` の api_request | `Failed to download changeset 12345: ...` | no |
| upload 側 412 | `upload_changes_with_auth` | `HTTP 412: ...`（本家の pass 分岐を潰した結果） | no |
| **HTTP 429 レート制限** | download / upload | `HTTP 429: ...` (リトライで解消しなかった場合) | **yes** |
| **HTTP 5xx** | download / upload | `HTTP {code}: ...` (リトライで解消しなかった場合) | **yes** |
| **ネットワーク断 / timeout** | どこでも | `Network: {e}` | **yes** |
| その他予期せぬ例外 | どこでも | `Unexpected: {type}: {e}` | no |

## テスト方針

### 単体テスト（実 API を叩かない）

- `test_parser.py`: コメント、空行、不正 ID、ファイル末尾改行有無
- `test_failure_writer.py`: 書き出した failed.txt をパーサに食わせて ID 復元できる
  round-trip テスト
- `test_error_classify.py`: RevertError / HTTPError / 汎用例外の分類が期待通り

### 手動テスト（マニュアル）

自分の個人アカウントで小さな changeset を 1〜2 件作り、以下の手順で確認:

1. `--dry-run` で `.osc` が正しく生成される
2. 実際に upload して OSM 側で復元されている
3. 意図的に redaction 済み changeset を含めて、失敗一覧に反映される

E2E を自動化しない理由: OSM 本番 API に副作用を残すため、CI では回せない。

## セキュリティ / リスク

- **OAuth token**: プロセス内メモリのみ、永続化なし。プロセス終了で破棄
- **入力ファイルの信頼**: changeset ID は user 指定を全面信頼、追加の allow-list なし
  （個人利用ツールなので）
- **意図しない大量 revert**: `--dry-run` を安全側デフォルトにはしない
  （明示的に指定させる）。ただし失敗時の再実行入力（`.failed.txt`）に comment を
  そのまま残すので、履歴は追える

## 非目標: `restore_version` 側の対応

`simple_revert` パッケージには `restore_version` コマンドもあるが、本ツールは
**changeset revert のみ**を扱う。オブジェクト単位の restore を batch 化したく
なった時点で別ツールとして切り出す。

## exit code の設計

| コード | 意味 |
|---|---|
| 0 | 全 changeset が「成功」 or 「差分なし (`no changes to revert/upload`)」 |
| 1 | 1 件以上失敗、ただし最後まで走り切った |
| 2 | 実行前エラー（入力ファイル無し / パース失敗 / `read_auth()` 失敗） |
| 3 | 途中で circuit break / KeyboardInterrupt → `<input>.remaining.txt` 生成 |

`read_auth()` は step 2 で呼ぶ。ここで例外が飛んだ場合は catch せず、
main で `SystemExit(2)` に変換して終了する（changeset のループには入らない）。

exit 1 と exit 3 の違い: **exit 1 は最後まで走った結果 (`.failed.txt` が
"確定した" 失敗一覧)**、**exit 3 は中断状態 (`.remaining.txt` に未処理あり、
`.failed.txt` は中断時点までの内容)**。再実行時に見るべきファイルが違う。

## 未確定事項

なし。実装フェーズで判断すべき細部（例: `print_status` を stderr にそのまま流す
か抑制するか）は writing-plans フェーズで詰める。
