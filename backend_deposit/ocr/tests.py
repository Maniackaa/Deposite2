from django.test import TestCase

# Create your tests here.
pairs = [(1, 2), (2, 3)]

x = zip((1, x[0], x[1]) for x in pairs)

for y in x:
    print(y)
