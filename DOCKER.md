# docker composeでの起動方法

```bash
$ chmod +x entrypoint.sh
$ docker compose -f compose.yml up -d

# コンテナに入る
$ docker compose -f compose.yml exec kimi-server

# ホスト側のブラウザで
localhost:8891/
```