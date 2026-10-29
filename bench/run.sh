#!/bin/sh
# Builds every benchmark in Plain and C, runs each with Python too, and prints the best of N runs.
# Usage: bench/run.sh [runs]     (default 3; Python runs once, it is slow)
set -e
cd "$(dirname "$0")"
RUNS=${1:-3}
OUT=$(mktemp -d)
best() {   # best wall time in seconds of running "$2..." $1 times
    n=$1; shift; b=""
    for _ in $(seq "$n"); do
        s=$(date +%s.%N); "$@" >/dev/null; e=$(date +%s.%N)
        t=$(echo "$e - $s" | bc)
        if [ -z "$b" ] || [ "$(echo "$t < $b" | bc)" = 1 ]; then b=$t; fi
    done
    printf "%.2f" "$b"
}
printf "%-8s %8s %8s %8s %10s\n" test C Plain Python "Plain/C"
for name in primes nbody fib words text; do
    clang -O2 "$name.c" -o "$OUT/$name.c" -lm
    python3 ../engc.py build "$name.eng" -o "$OUT/$name.plain" >/dev/null
    c=$(best "$RUNS" "$OUT/$name.c")
    p=$(best "$RUNS" "$OUT/$name.plain")
    py=$(best 1 python3 "$name.py")
    printf "%-8s %8s %8s %8s %10s\n" "$name" "$c" "$p" "$py" "$(echo "scale=1; $p / $c" | bc)"
done
rm -rf "$OUT"
