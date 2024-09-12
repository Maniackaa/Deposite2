import re

distinct_recipients = [{'recipient': '+994 51 719 06 57'}, {'recipient': '+994 51 719 06 42'}, {'recipient': '+994 51 719 06 58'}, {'recipient': '+994 51 719 06 54'}, {'recipient': '+994 51 719 06 51'}, {'recipient': '+994 51 732 82 97'}, {'recipient': '+994 51 783 54 97'}, {'recipient': '+994 77 802 71 54'}, {'recipient': '+994 51 785 47 76'}, {'recipient': '+994 51 719 06 94'}, {'recipient': '+994 51 719 06 27'}, {'recipient': '+994 51 783 53 81'}, {'recipient': '+994 51 785 48 32'}, {'recipient': '+994 51 719 24 36'}, {'recipient': '+994 51 719 24 35'}, {'recipient': '+994 51 974 59 13'}, {'recipient': '+994 51 783 54 06'}, {'recipient': '+994 70 664 07 01'}, {'recipient': '+994 70 944 07 01'}, {'recipient': '+994 70 222 57 79'}, {'recipient': '+994 55 384 43 71'}, {'recipient': '+994 55 521 67 91'}, {'recipient': '+994 51 498 63 53'}, {'recipient': '+994 51 719 23 82'}, {'recipient': '+994 51 719 23 84'}, {'recipient': '+994 51 719 23 68'}, {'recipient': '+994 51 783 54 72'}, {'recipient': '+994 55 761 88 49'}, {'recipient': '+994 55 570 86 73'}, {'recipient': '+994 55 779 43 06'}, {'recipient': '+994 55 530 93 44'}, {'recipient': '+994 51 719 07 38'}]
distinct_recipients = ['f']
result = []
for incoming in sorted(distinct_recipients,
                       key=lambda x: bool(re.findall(r'\d\d\d \d\d \d\d\d \d\d \d\d', x['recipient']))):
    print(incoming)
    result.append((incoming['recipient'], incoming['recipient']))