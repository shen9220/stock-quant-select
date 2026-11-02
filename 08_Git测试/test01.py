#!/usr/bin/env python3
"""终端小游戏：猜 1～100 的数字，7 次内猜中即胜。"""
import random

def main():
    n = random.randint(1, 100)
    print("猜数字（1～100），你有 7 次机会。")
    for i in range(7, 0, -1):
        try:
            g = int(input(f"还剩 {i} 次，请输入整数: ").strip())
        except ValueError:
            print("请输入有效整数。")
            continue
        if g < n:
            print("太小了。")
        elif g > n:
            print("太大了。")
        else:
            print(f"猜对了！答案就是 {n}。")
            return
    print(f"次数用尽，答案是 {n}。")

if __name__ == "__main__":
    main()
