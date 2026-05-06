#!/usr/bin/env python3
"""
贪吃蛇小游戏
操作: 方向键/WASD 控制方向, P 暂停, Q/ESC 退出
依赖: pip install pygame
"""

import pygame
import random
import sys

# ── 配置 ──────────────────────────────────────────────
CELL_SIZE = 25
COLS, ROWS = 28, 22
WIDTH, HEIGHT = CELL_SIZE * COLS, CELL_SIZE * ROWS
FPS = 10

# 颜色
BG_COLOR      = (24, 24, 32)
GRID_COLOR    = (32, 32, 44)
SNAKE_HEAD    = (0, 230, 118)
SNAKE_BODY    = (0, 200, 83)
SNAKE_OUTLINE = (0, 150, 60)
FOOD_COLOR    = (255, 82, 82)
FOOD_GLOW     = (255, 82, 82, 60)
SCORE_COLOR   = (220, 220, 220)
GAMEOVER_BG   = (0, 0, 0, 180)

# 方向
UP    = (0, -1)
DOWN  = (0, 1)
LEFT  = (-1, 0)
RIGHT = (1, 0)


class SnakeGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("🐍 贪吃蛇")
        self.clock = pygame.time.Clock()
        self.font_big = pygame.font.SysFont("monospace", 48, bold=True)
        self.font_med = pygame.font.SysFont("monospace", 28)
        self.font_sm  = pygame.font.SysFont("monospace", 18)
        self.reset()

    def reset(self):
        cx, cy = COLS // 2, ROWS // 2
        self.snake = [(cx, cy), (cx - 1, cy), (cx - 2, cy)]
        self.direction = RIGHT
        self.next_direction = RIGHT
        self.score = 0
        self.alive = True
        self.paused = False
        self.spawn_food()

    def spawn_food(self):
        while True:
            pos = (random.randint(0, COLS - 1), random.randint(0, ROWS - 1))
            if pos not in self.snake:
                self.food = pos
                return

    # ── 逻辑 ──────────────────────────────────────────
    def update(self):
        if not self.alive or self.paused:
            return

        self.direction = self.next_direction
        hx, hy = self.snake[0]
        dx, dy = self.direction
        new_head = (hx + dx, hy + dy)

        # 碰墙或碰自己
        nx, ny = new_head
        if nx < 0 or nx >= COLS or ny < 0 or ny >= ROWS:
            self.alive = False
            return
        if new_head in self.snake:
            self.alive = False
            return

        self.snake.insert(0, new_head)
        if new_head == self.food:
            self.score += 10
            self.spawn_food()
        else:
            self.snake.pop()

    # ── 绘制 ──────────────────────────────────────────
    def draw_grid(self):
        for x in range(0, WIDTH, CELL_SIZE):
            pygame.draw.line(self.screen, GRID_COLOR, (x, 0), (x, HEIGHT))
        for y in range(0, HEIGHT, CELL_SIZE):
            pygame.draw.line(self.screen, GRID_COLOR, (0, y), (WIDTH, y))

    def draw_snake(self):
        for i, (x, y) in enumerate(self.snake):
            rect = pygame.Rect(x * CELL_SIZE, y * CELL_SIZE, CELL_SIZE, CELL_SIZE)
            color = SNAKE_HEAD if i == 0 else SNAKE_BODY
            pygame.draw.rect(self.screen, color, rect, border_radius=6)
            pygame.draw.rect(self.screen, SNAKE_OUTLINE, rect, width=2, border_radius=6)
            # 眼睛
            if i == 0:
                cx = x * CELL_SIZE + CELL_SIZE // 2
                cy = y * CELL_SIZE + CELL_SIZE // 2
                dx, dy = self.direction
                eye_off = 5
                e1 = (cx + dx * 4 - dy * eye_off, cy + dy * 4 + dx * eye_off)
                e2 = (cx + dx * 4 + dy * eye_off, cy + dy * 4 - dx * eye_off)
                for ex, ey in [e1, e2]:
                    pygame.draw.circle(self.screen, (255, 255, 255), (ex, ey), 3)
                    pygame.draw.circle(self.screen, (0, 0, 0), (ex, ey), 1)

    def draw_food(self):
        x, y = self.food
        cx = x * CELL_SIZE + CELL_SIZE // 2
        cy = y * CELL_SIZE + CELL_SIZE // 2
        # 光晕
        glow_surf = pygame.Surface((CELL_SIZE * 2, CELL_SIZE * 2), pygame.SRCALPHA)
        pygame.draw.circle(glow_surf, FOOD_GLOW, (CELL_SIZE, CELL_SIZE), CELL_SIZE)
        self.screen.blit(glow_surf, (cx - CELL_SIZE, cy - CELL_SIZE))
        # 食物
        pygame.draw.circle(self.screen, FOOD_COLOR, (cx, cy), CELL_SIZE // 2 - 2)
        pygame.draw.circle(self.screen, (255, 150, 150), (cx - 3, cy - 3), 3)

    def draw_score(self):
        txt = self.font_sm.render(f"Score: {self.score}", True, SCORE_COLOR)
        self.screen.blit(txt, (10, 6))

    def draw_overlay(self, lines, sub_lines=None):
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill(GAMEOVER_BG)
        self.screen.blit(overlay, (0, 0))

        y = HEIGHT // 2 - len(lines) * 30
        for line in lines:
            txt = self.font_big.render(line, True, (255, 255, 255))
            self.screen.blit(txt, (WIDTH // 2 - txt.get_width() // 2, y))
            y += 50
        if sub_lines:
            y += 10
            for line in sub_lines:
                txt = self.font_sm.render(line, True, (180, 180, 180))
                self.screen.blit(txt, (WIDTH // 2 - txt.get_width() // 2, y))
                y += 30

    def draw(self):
        self.screen.fill(BG_COLOR)
        self.draw_grid()
        self.draw_food()
        self.draw_snake()
        self.draw_score()

        if not self.alive:
            self.draw_overlay(
                ["GAME OVER"],
                [f"得分: {self.score}", "按 R 重新开始  |  Q 退出"],
            )
        elif self.paused:
            self.draw_overlay(["PAUSED"], ["按 P 继续"])

        pygame.display.flip()

    # ── 事件 ──────────────────────────────────────────
    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False

            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_q, pygame.K_ESCAPE):
                    return False

                if event.key == pygame.K_r:
                    self.reset()
                    continue

                if event.key == pygame.K_p:
                    self.paused = not self.paused
                    continue

                if not self.alive or self.paused:
                    continue

                direction_map = {
                    pygame.K_UP: UP,    pygame.K_w: UP,
                    pygame.K_DOWN: DOWN,  pygame.K_s: DOWN,
                    pygame.K_LEFT: LEFT,  pygame.K_a: LEFT,
                    pygame.K_RIGHT: RIGHT, pygame.K_d: RIGHT,
                }
                new_dir = direction_map.get(event.key)
                if new_dir:
                    # 禁止 180° 掉头
                    cx, cy = self.direction
                    nx, ny = new_dir
                    if (cx + nx, cy + ny) != (0, 0):
                        self.next_direction = new_dir

        return True

    # ── 主循环 ─────────────────────────────────────────
    def run(self):
        running = True
        while running:
            running = self.handle_events()
            self.update()
            self.draw()
            self.clock.tick(FPS)

        pygame.quit()
        sys.exit()


if __name__ == "__main__":
    SnakeGame().run()