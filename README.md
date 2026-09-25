# kimimachi mapgen — 地名 → ピクセルゲームマップ

地名を入れると、その場所の地図データを取得して「抽象化した地図画像」を作り、16px タイルのゲームマップに変換します。

```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m mapgen "新潟県長岡市"                 # 2km四方, 1マス8m -> 250x250
.venv/bin/python -m mapgen "長岡駅" --size 1000 --tile-m 5
.venv/bin/python -m mapgen "新潟県長岡市" --straighten 6      # より大胆に直線化
.venv/bin/python -m mapgen "新潟県長岡市" --layout real       # 実際の形のまま
.venv/bin/python -m mapgen "浅草橋" --lat 35.6962 --lon 139.7825 --size 1400 --title "浅草橋・東神田"
.venv/bin/python -m mapgen "向ヶ丘遊園駅" --size 1400 --title "向ヶ丘遊園"
.venv/bin/python -m mapgen "渋谷駅" --source google       # 要 GOOGLE_MAPS_API_KEY
```

## Web UI

```bash
.venv/bin/python -m mapgen.server --port 8891     # out/ の配信と生成API
tailscale serve --bg --https=8446 http://127.0.0.1:8891
```
一覧ページのフォームに地名を入れると、裏で生成して、終わるとそのマップを開く（`POST /api/generate`、`GET /api/jobs/<id>`）。

## パイプライン

1. **ジオコーディング**: Nominatim (OSM)、見つからなければ国土地理院の住所検索
2. **地図取得 → 意味画像化** (`semantic_map.png`, 1px = tile_m/8 m)
   - `plateau` (既定): 範囲内の市区町村を地理院の逆ジオコーダで調べ（複数の区にまたがってもよい）、PLATEAU の土地利用 (luse) と道路 (tran) の MVT を取得。建物・鉄道・水域・注記は国土地理院ベクトルタイルで補う。PLATEAU がない都市では自動で `gsi` に切り替える
   - `gsi`: 国土地理院ベクトルタイルだけを使う（全国で使えるが土地被覆がないので、草地が多めになる）
   - `google`: Static Maps を「ラベルなし・地物ごとに単色」のスタイルで取得し、色から分類する（未検証。下の注意を参照）
3. **模式化** (`schematic.py`, 既定 `--layout schematic`) — 人が頭の中に持っている地図に近づける
   - 斜めになる道路（軸から22.5°以上ずれるもの）の総延長が最も短くなる角度を、±45°の範囲で0.5°刻みに探して回転する（`--rotate auto`、既定）。線路は3倍の重みで数える。`--rotate rail` で線路を `--rail-axis` に合わせ、`--rotate none` で北を真上に固定する
   - 道路・線路をグラフにして、交差点から交差点までを単純化する。各区間は水平／垂直（斜め30〜60°なら45°）に拘束し、最小二乗で位置を解いたあと、水平・垂直の区間をぴったりそろえる
   - 直線化で横に `--straighten` タイル以上ずれる区間は元の形のまま残す（大きな歪みを防ぐため）
   - 道路網がどう動いたかから滑らかな変位場を作り、建物・土地利用・川・地名を一緒に移動させる
   - それでも斜めのまま残った区間は「直線→45°→直線」に分ける（端点は動かさない）。これで全部の道が水平・垂直・45°のどれかになる
   - 45°の区間は斜めパーツ（`diagonal.py`）で描く。中心タイル＋両脇の三角タイルを重ね描き用レイヤーに置き、なめらかな斜めの道・線路にする（`--no-diagonal` で無効）
   - `--layout real` にすると実際の形のまま出力する
4. **抽象化** (`abstract.py`): 8×8px ごとに占有率を見てタイル種別を決める
   - 建物 40%以上 → 建物（周囲の土地利用から 家 / 店・ビル / 工場 / 公共施設 を判定）
   - 道路・線路は中心線が通るマスを必ず含める（連結性の保証）。斜めだけでつながっている箇所は4近傍でつなぎ直す
   - 開けた水面に接する道路は橋、ノイズは多数決で除去、並んだ家は2×3マス単位で別の屋根に分ける
5. **タイル化** (`tileset.py`): 手続き生成の 16px タイルセットを使い、水・道路・線路・森・建物は 4近傍マスクでオートタイル

## 出力 (`out/<地名>/`)

| ファイル | 内容 |
|---|---|
| `index.html` | 歩き回れるビューア（単体で動く。矢印/WASD、Shift、M、+/-、スマホは十字キー） |
| `map.tmj` + `tileset.png` | Tiled 形式（`ground` と、斜めパーツ用の `overlay` の2レイヤー。タイルに `kind` / `collides` プロパティ、地名は object layer） |
| `map.json` | ゲーム用の簡易データ（kindGrid, tileGrid, labels, blockingKinds, meta） |
| `map.png` | マップ全体の画像 |
| `semantic_map.png` / `abstract.png` | 中間画像（意味画像 / 1マス=4px の種別画像） |

## 出典・利用データ

このツールが生成するマップ（`out/` の PNG・`map.tmj`・`map.json`・`index.html`）は、以下のデータを**加工して作成**したものです。
マップを公開・配布するときは、ここに書いた出典と、加工したことを必ず表示してください。
生成したマップには出典が自動で入ります（ビューアの右下、`map.json` の `meta.attribution`、`map.tmj` の map プロパティ）。

### 3D都市モデル（Project PLATEAU）— 国土交通省

- 使用データ: 土地利用モデル（luse）・交通（道路）モデル（tran）の MVT 配信
  （[PLATEAU データカタログAPI](https://api.plateau.reearth.io/datacatalog/plateau-datasets) と
  [G空間情報センター](https://www.geospatial.jp/ckan/dataset/plateau) で公開されているもの）
- 権利: 著作権は各地方公共団体に帰属します
- 利用条件: [PLATEAU サイトポリシー](https://www.mlit.go.jp/plateau/site-policy/)「3. コンテンツの利用」に従います。
  これは[公共データ利用規約（第1.0版）（PDL1.0）](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)に準拠し、CC BY 4.0 と互換です。
  加工して使うときは、出典とは別に加工したことを書く必要があります。また、国土交通省が作ったかのように見せてはいけません
- 出典表記の例（年度と市区町村はマップごとに自動で入ります）:

  > 出典：3D都市モデル（Project PLATEAU）長岡市（2024年度）（国土交通省）を加工して作成

- 測量法について: PLATEAU は公共測量の成果をもとにしているため、使い方によっては測量法の手続きが必要になることがあります。
  出版物や商用で使う前に、[3D都市モデル整備のための測量マニュアル](https://www.mlit.go.jp/plateau/libraries/handbooks/)を確認してください

### 国土地理院最適化ベクトルタイル — 国土地理院

- 使用データ: `experimental_bvmap`（建物・道路中心線・鉄道・水域・注記）
  （[gsi-cyberjapan/optimal_bvmap](https://github.com/gsi-cyberjapan/optimal_bvmap)）
- 利用条件: [国土地理院コンテンツ利用規約](https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html)
- 出典表記:

  > 出典：国土地理院最適化ベクトルタイルを加工して作成

- あわせて地理院地図の住所検索（`msearch.gsi.go.jp`）と逆ジオコーダ（`mreversegeocoder.gsi.go.jp`）も使っています
  （地名検索の予備と、範囲内の市区町村を調べるため）

### 地名検索 — Nominatim / OpenStreetMap

- 地名から座標への変換（と `meta.display_name` の住所文字列）に [Nominatim](https://nominatim.org/) を使っています
- データ: © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors（[ODbL](https://opendatacommons.org/licenses/odbl/)）
- [Nominatim の利用ポリシー](https://operations.osmfoundation.org/policies/nominatim/)（1秒に1回まで、User-Agent の明示）を守るため、結果は `.cache/` にキャッシュします。大量に生成するときは自前の Nominatim を立てるなどしてください

### Google Maps（`--source google` を使った場合だけ）

- 出典表記: Map data ©Google
- [Google Maps Platform の利用規約](https://cloud.google.com/maps-platform/terms)では、地図画像から地物データを抽出したり、派生データを作ったりすることが制限されています。
  そのため既定は PLATEAU／地理院で、`google` は検証用です。生成物を公開しないでください

### まとめて書く場合の例

```
出典：3D都市モデル（Project PLATEAU）長岡市（2024年度）（国土交通省）、国土地理院最適化ベクトルタイル を加工して作成
地名検索：Nominatim / © OpenStreetMap contributors
```

## 注意
- 取得データは `.cache/` にキャッシュされる
- このリポジトリのソースコード自体のライセンスはまだ決めていません
