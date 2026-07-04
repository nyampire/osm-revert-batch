# osm-revert-batch

`simple_revert` の Python API を使い、ファイルに列挙した OSM changeset を
順に revert するバッチラッパー。

- 1 プロセスで OAuth 1 回、以降複数 changeset を続けて処理
- 失敗した changeset は入力と同形式の `<input>.failed.txt` に理由付きで書き出し
- `--dry-run` で OAuth 抜きの `.osc` 出力のみ

詳細な設計は [docs/design.md](docs/design.md) を参照。
