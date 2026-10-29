# The same algorithm as primes.eng, written plainly in Python.
limit = 5000000
crossed = []
for n in range(1, limit + 1):
    crossed.append(0)

count = 0
for n in range(2, limit + 1):
    if crossed[n - 1] == 0:
        count += 1
        multiple = n * n
        while multiple <= limit:
            crossed[multiple - 1] = 1
            multiple += n
print(count)
