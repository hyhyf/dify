#!/bin/bash
# ============================================================================
# Dify 并发工作流测试脚本
# 
# 测试多个工作流能否并行执行。分别通过 blocking 和 streaming 模式验证：
# 1. 并行提交 N 个请求
# 2. 记录每个请求的耗时和总耗时
# 3. 对比：若并行 → 总耗时 ≈ max(各请求耗时)；若串行 → 总耗时 ≈ sum(各请求耗时)
#
# 用法: bash concurrent_workflow_test.sh [并发数] [模式(blocking|streaming)]
# ============================================================================

set -e

CONCURRENT=${1:-3}
MODE=${2:-blocking}
API_URL="http://localhost/v1/chat-messages"
API_TOKEN="app-f72Dc8iCmSh1vnTIukNNfXTX"
QUERY="获取麦当劳门店"
TIMEOUT=120
OUTPUT_DIR="/tmp/dify_concurrent_test_$$"

mkdir -p "$OUTPUT_DIR"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║        Dify 并发工作流测试                                ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║ API:          $API_URL"
echo "║ Mode:         $MODE"
echo "║ Concurrency:  $CONCURRENT"
echo "║ Query:        $QUERY"
echo "║ Timeout:      ${TIMEOUT}s per request"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Check dependencies
for cmd in curl jq bc; do
    if ! command -v $cmd &>/dev/null; then
        echo "[ERROR] Missing dependency: $cmd"
        exit 1
    fi
done

# Cleanup function
cleanup() {
    rm -rf "$OUTPUT_DIR"
    pkill -P $$ 2>/dev/null || true
}
trap cleanup EXIT

echo "[INFO] Starting Celery worker log monitor..."
docker compose -f /root/dify/docker/docker-compose.yaml logs -f --tail 0 worker 2>&1 | \
    grep --line-buffered -E "task.*received|task.*succeeded|Starting Celery|concurrency" > "$OUTPUT_DIR/worker.log" &
LOG_MONITOR_PID=$!
sleep 1

# Function: submit a single request and measure time
submit_request() {
    local idx=$1
    local user="concurrent-test-${idx}-$(date +%s)"
    local output_file="$OUTPUT_DIR/result_${idx}.json"
    local time_file="$OUTPUT_DIR/time_${idx}.txt"

    local req_start req_end duration http_code

    req_start=$(date +%s.%N)

    if [ "$MODE" == "streaming" ]; then
        http_code=$(curl -s -o "$output_file" -w "%{http_code}" \
            --max-time "$TIMEOUT" \
            -X POST "$API_URL" \
            -H "Authorization: Bearer $API_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"inputs\": {}, \"query\": \"$QUERY\", \"response_mode\": \"streaming\", \"user\": \"$user\"}")
    else
        http_code=$(curl -s -o "$output_file" -w "%{http_code}" \
            --max-time "$TIMEOUT" \
            -X POST "$API_URL" \
            -H "Authorization: Bearer $API_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"inputs\": {}, \"query\": \"$QUERY\", \"response_mode\": \"blocking\", \"user\": \"$user\"}")
    fi

    req_end=$(date +%s.%N)
    duration=$(echo "scale=2; $req_end - $req_start" | bc)
    echo "$duration" > "$time_file"

    echo "[#$idx] user=$user HTTP=$http_code duration=${duration}s"
}

echo "[INFO] Submitting $CONCURRENT concurrent requests..."
echo ""

# Record global start time
GLOBAL_START=$(date +%s.%N)

# Submit all requests in parallel
for i in $(seq 1 "$CONCURRENT"); do
    submit_request "$i" &
    pids[$i]=$!
done

# Wait for all background jobs
for pid in ${pids[*]}; do
    wait "$pid" 2>/dev/null || true
done

# Record global end time
GLOBAL_END=$(date +%s.%N)
GLOBAL_DURATION=$(echo "scale=2; $GLOBAL_END - $GLOBAL_START" | bc)

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║                      测试结果                            ║"
echo "╠══════════════════════════════════════════════════════════╣"

# Collect individual durations
declare -a durations
sum_duration=0
max_duration=0

for i in $(seq 1 "$CONCURRENT"); do
    d=$(cat "$OUTPUT_DIR/time_${i}.txt" 2>/dev/null || echo "0")
    durations[$i]=$d
    sum_duration=$(echo "scale=2; $sum_duration + $d" | bc)
    if [ "$(echo "$d > $max_duration" | bc)" -eq 1 ]; then
        max_duration=$d
    fi

    # Show answer snippet
    answer=$(jq -r '.answer // .message // "N/A"' "$OUTPUT_DIR/result_${i}.json" 2>/dev/null | head -c 80)
    echo "║ Request #$i: ${durations[$i]}s | $answer"
done

echo "╠══════════════════════════════════════════════════════════╣"
echo "║ Total wall clock:  ${GLOBAL_DURATION}s"
echo "║ Sum of durations:  ${sum_duration}s"
echo "║ Max single time:   ${max_duration}s"
echo "╠══════════════════════════════════════════════════════════╣"

# Determine concurrency
concurrency_ratio=$(echo "scale=2; $sum_duration / $GLOBAL_DURATION" | bc | sed 's/^\./0./')

if [ "$(echo "$GLOBAL_DURATION < $sum_duration * 0.6" | bc)" -eq 1 ]; then
    echo "║ ✓ CONCURRENT EXECUTION DETECTED"
    echo "║   (total time significantly less than sum)"
elif [ "$(echo "$GLOBAL_DURATION > $sum_duration * 0.85" | bc)" -eq 1 ]; then
    echo "║ ✗ SERIAL EXECUTION DETECTED"
    echo "║   (total time ≈ sum of individual times)"
else
    echo "║ ~ PARTIALLY CONCURRENT"
    echo "║   (some overlap, concurrency ratio: ${concurrency_ratio}x)"
fi

echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Worker log summary
echo "[INFO] Celery worker activity during test:"
sleep 2
kill $LOG_MONITOR_PID 2>/dev/null || true
echo "  Tasks received:   $(grep -c "received" "$OUTPUT_DIR/worker.log" 2>/dev/null || echo 0)"
echo "  Tasks succeeded:  $(grep -c "succeeded" "$OUTPUT_DIR/worker.log" 2>/dev/null || echo 0)"
echo "  Concurrency:      $(grep "concurrency:" "$OUTPUT_DIR/worker.log" 2>/dev/null | tail -1 || echo 'N/A')"

echo ""
echo "[DONE] Test completed. Logs saved to: $OUTPUT_DIR"
