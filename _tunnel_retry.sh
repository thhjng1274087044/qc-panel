#!/bin/bash
# 反复申请 Cloudflare Quick Tunnel，直到真正注册到边缘节点。
# 只拿到地址不算成功 —— 必须出现 "Registered tunnel connection"。
cd "$(dirname "$0")" || exit 1

for i in $(seq 1 12); do
  LOG=/tmp/cf_$i.log
  rm -f "$LOG"
  ./cloudflared.exe tunnel --url http://localhost:8776 --no-autoupdate --protocol http2 > "$LOG" 2>&1 &
  pid=$!
  ok=0
  for s in $(seq 1 30); do
    sleep 2
    if grep -q "Registered tunnel connection" "$LOG" 2>/dev/null; then ok=1; break; fi
    kill -0 "$pid" 2>/dev/null || break
  done

  if [ "$ok" = "1" ]; then
    URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | head -1)
    echo "TUNNEL_OK $URL"
    # 保持前台等待，进程不退出 → 隧道持续可用
    wait "$pid"
    exit 0
  fi

  echo "attempt $i: $(grep -m1 -E 'failed to request|context deadline' "$LOG" || echo 'edge connect failed')"
  kill "$pid" 2>/dev/null
  sleep 3
done

echo "ALL_FAILED"
exit 1
