import itertools
import time

from django.test import TestCase

# Create your tests here.
start = time.perf_counter()
all_values = range(0, 256)
comb = list(itertools.permutations(all_values, 2))
empty_pairs = []
ready_pairs = []
for pair in comb:
    if pair in ready_pairs:
        continue
    empty_pairs.append(pair)
# print(empty_pairs)
print(time.perf_counter() - start)


bad_pairs = []
black_range = range(0, 29)
white_range = range(0, 256)
for black in black_range:
    for white in white_range:
        bad_pairs.append((black, white))
print(bad_pairs)
print(time.perf_counter() - start)


