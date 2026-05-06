#!/usr/bin/env python3
"""
推箱子 (Sokoban) - 终端版
操作: 方向键/WASD 移动, U 撤销, R 重玩, N 下一关, Q 退出
"""

import curses
import copy

# ── 地图元素 ──────────────────────────────────────────
WALL   = '#'
FLOOR  = ' '
BOX    = '$'
TARGET = '.'
PLAYER = '@'
BOX_ON = '*'   # 箱子在目标上
PLAYER_ON = '+' # 玩家在目标上

# ── 关卡 ──────────────────────────────────────────────
LEVELS = [
    # 关卡 1 - 入门
    [
        "  ####  ",
        "###  ###",
        "#  . $ #",
        "# #  # #",
        "#   $  #",
        "###  ###",
        "  #  #  ",
        "  #@#  ",
        "  ###  ",
    ],
    # 关卡 2
    [
        " #####  ",
        " # . #  ",
        " #$  #  ",
        "##  $## ",
        "#   . # ",
        "# @#  # ",
        "########",
    ],
    # 关卡 3
    [
        "  ######",
        "  #    #",
        "###$## #",
        "# . $  #",
        "# .$ ##",
        "# .#@  #",
        "########",
    ],
    # 关卡 4
    [
        " ####   ",
        "##  ### ",
        "#   $ # ",
        "# #.# # ",
        "#   $ # ",
        "##  ### ",
        " #.@#  ",
        " ####   ",
    ],
    # 关卡 5
    [
        "####### ",
        "#  .  # ",
        "# #$# # ",
        "#  $  # ",
        "##.@.##",
        " # $ #  ",
        " # # #  ",
        " #   #  ",
        " #####  ",
    ],
    # 关卡 6
    [
        "  ##### ",
        "###   # ",
        "# $ # ##",
        "# #  . #",
        "# . $# #",
        "## #   #",
        " #@ ###",
        " ####   ",
    ],
    # 关卡 7
    [
        "########",
        "#  .   #",
        "# ## $ #",
        "#  .$## ",
        "##.$   #",
        " # .@$# ",
        " ###### ",
    ],
]


class Sokoban:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.level = 0
        self.history = []
        self.won = False
        self.total_moves = 0
        self.init_colors()
        self.load_level()

    def init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_YELLOW, -1)   # 墙壁
        curses.init_pair(2, curses.COLOR_RED, -1)       # 箱子
        curses.init_pair(3, curses.COLOR_GREEN, -1)      # 目标
        curses.init_pair(4, curses.COLOR_CYAN, -1)       # 玩家
        curses.init_pair(5, curses.COLOR_WHITE, -1)       # 箱子在目标上
        curses.init_pair(6, curses.COLOR_MAGENTA, -1)    # 玩家在目标上
        curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_YELLOW)  # 完成标记

    def parse_map(self, raw_map):
        grid = []
        player = None
        boxes = []
        targets = []
        max_w = max(len(row) for row in raw_map)

        for y, row in enumerate(raw_map):
            grid_row = []
            for x in range(max_w):
                ch = row[x] if x < len(row) else ' '
                if ch in (PLAYER, PLAYER_ON):
                    player = (x, y)
                if ch in (BOX, BOX_ON):
                    boxes.append((x, y))
                if ch in (TARGET, BOX_ON, PLAYER_ON):
                    targets.append((x, y))
                # 存储基础地图（去掉动态元素）
                if ch in (PLAYER, PLAYER_ON):
                    grid_row.append(TARGET if ch == PLAYER_ON else FLOOR)
                elif ch in (BOX, BOX_ON):
                    grid_row.append(TARGET if ch == BOX_ON else FLOOR)
                else:
                    grid_row.append(ch)
            grid.append(grid_row)

        return grid, player, boxes, targets

    def load_level(self):
        if self.level >= len(LEVELS):
            self.level = 0
        raw = LEVELS[self.level]
        self.grid, self.player, self.boxes, self.targets = self.parse_map(raw)
        self.history = []
        self.won = False
        self.moves = 0

    def save_state(self):
        self.history.append((
            copy.deepcopy(self.boxes),
            self.player,
            self.moves,
        ))

    def undo(self):
        if self.history:
            self.boxes, self.player, self.moves = self.history.pop()

    def is_wall(self, x, y):
        if y < 0 or y >= len(self.grid) or x < 0 or x >= len(self.grid[0]):
            return True
        return self.grid[y][x] == WALL

    def has_box(self, x, y):
        return (x, y) in self.boxes

    def move(self, dx, dy):
        if self.won:
            return

        px, py = self.player
        nx, ny = px + dx, py + dy  # 目标格

        if self.is_wall(nx, ny):
            return

        if self.has_box(nx, ny):
            bx, by = nx + dx, ny + dy  # 箱子要推到的位置
            if self.is_wall(bx, by) or self.has_box(bx, by):
                return
            self.save_state()
            self.boxes.remove((nx, ny))
            self.boxes.append((bx, by))
            self.player = (nx, ny)
            self.moves += 1
        else:
            self.save_state()
            self.player = (nx, ny)
            self.moves += 1

        # 检查胜利
        if all(b in self.targets for b in self.boxes):
            self.won = True
            self.total_moves += self.moves

    def draw(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()

        grid_h = len(self.grid)
        grid_w = max(len(row) for row in self.grid) if self.grid else 0

        # 每个格子占 2 个字符宽，保持方形
        cell_w = 2
        offset_y = max(0, (h - grid_h - 6) // 2)
        offset_x = max(0, (w - grid_w * cell_w) // 2)

        # 标题
        title = f"═══ 推箱子 第 {self.level + 1}/{len(LEVELS)} 关 ═══"
        try:
            self.stdscr.addstr(offset_y, max(0, (w - len(title)) // 2), title, curses.color_pair(1) | curses.A_BOLD)
        except curses.error:
            pass

        # 绘制地图
        for y, row in enumerate(self.grid):
            for x, cell in enumerate(row):
                sy = offset_y + 2 + y
                sx = offset_x + x * cell_w
                if sy >= h - 3 or sx >= w - 2:
                    continue

                is_player = (x, y) == self.player
                is_box = (x, y) in self.boxes
                is_target = (x, y) in self.targets

                if is_player and is_target:
                    ch = '@ '
                    attr = curses.color_pair(6) | curses.A_BOLD
                elif is_player:
                    ch = '@ '
                    attr = curses.color_pair(4) | curses.A_BOLD
                elif is_box and is_target:
                    ch = '★ '
                    attr = curses.color_pair(5) | curses.A_BOLD
                elif is_box:
                    ch = '□ '
                    attr = curses.color_pair(2) | curses.A_BOLD
                elif is_target:
                    ch = '◇ '
                    attr = curses.color_pair(3)
                elif cell == WALL:
                    ch = '██'
                    attr = curses.color_pair(1)
                else:
                    ch = '  '
                    attr = curses.A_NORMAL

                try:
                    self.stdscr.addstr(sy, sx, ch, attr)
                except curses.error:
                    pass

        # 底部信息
        info_y = offset_y + 2 + grid_h + 1
        if info_y < h - 1:
            status = f"步数: {self.moves}"
            try:
                self.stdscr.addstr(info_y, max(0, (w - len(status)) // 2), status, curses.color_pair(4))
            except curses.error:
                pass

        if self.won:
            msg1 = "🎉 恭喜过关！🎉"
            msg2 = "N 下一关 | R 重玩 | Q 退出"
            try:
                self.stdscr.addstr(info_y + 1, max(0, (w - len(msg1)) // 2), msg1, curses.color_pair(5) | curses.A_BOLD)
                self.stdscr.addstr(info_y + 2, max(0, (w - len(msg2)) // 2), msg2, curses.color_pair(3))
            except curses.error:
                pass
        else:
            help_text = "方向键移动 | U 撤销 | R 重玩 | Q 退出"
            try:
                self.stdscr.addstr(info_y + 1, max(0, (w - len(help_text)) // 2), help_text, curses.A_DIM)
            except curses.error:
                pass

        self.stdscr.refresh()

    def run(self):
        self.stdscr.keypad(True)
        curses.curs_set(0)

        while True:
            self.draw()
            key = self.stdscr.getch()

            if key in (ord('q'), ord('Q'), 27):  # Q / ESC
                break
            elif key in (ord('r'), ord('R')):
                self.load_level()
            elif key in (ord('u'), ord('U')):
                self.undo()
            elif key in (ord('n'), ord('N')) and self.won:
                self.level += 1
                self.load_level()
            elif key in (curses.KEY_UP, ord('w'), ord('W')):
                self.move(0, -1)
            elif key in (curses.KEY_DOWN, ord('s'), ord('S')):
                self.move(0, 1)
            elif key in (curses.KEY_LEFT, ord('a'), ord('A')):
                self.move(-1, 0)
            elif key in (curses.KEY_RIGHT, ord('d'), ord('D')):
                self.move(1, 0)


def main(stdscr):
    curses.curs_set(0)
    game = Sokoban(stdscr)
    game.run()


if __name__ == "__main__":
    curses.wrapper(main)