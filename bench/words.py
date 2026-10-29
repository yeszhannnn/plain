vocabulary = ["w " + str(n) for n in range(1, 10001)]
count = {}
seed = 1
for n in range(5000000):
    seed = (seed * 1103515245 + 12345) % 2147483648
    w = vocabulary[seed % 10000]
    count[w] = count.get(w, 0) + 1
print(len(count), count["w 1"])
