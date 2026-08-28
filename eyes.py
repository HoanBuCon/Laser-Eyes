import math
import sys
import time

SCREEN_W = 80
SCREEN_H = 24

THETA_STEP = 0.07
PHI_STEP = 0.02

R1 = 1.0
R2 = 2.0
K2 = 5.0
K1 = SCREEN_W * K2 * 3 / (8 * (R1 + R2))

SHADE = ".,-~:;=!*#$@"

def render_frame(A, B):
    output = [' '] * (SCREEN_W * SCREEN_H)
    zbuffer = [0.0] * (SCREEN_W * SCREEN_H)

    cos_A, sin_A = math.cos(A), math.sin(A)
    cos_B, sin_B = math.cos(B), math.sin(B)

    theta = 0.0
    while theta < 2 * math.pi:
        cos_t, sin_t = math.cos(theta), math.sin(theta)

        phi = 0.0
        while phi < 2 * math.pi:
            cos_p, sin_p = math.cos(phi), math.sin(phi)

            circle_x = R2 + R1 * cos_t
            circle_y = R1 * sin_t

            x = circle_x * (cos_B * cos_p + sin_A * sin_B * sin_p) - circle_y * cos_A * sin_B
            y = circle_x * (sin_B * cos_p - sin_A * cos_B * sin_p) + circle_y * cos_A * cos_B
            z = K2 + cos_A * circle_x * sin_p + circle_y * sin_A
            ooz = 1.0 / z

            xp = int(SCREEN_W / 2 + K1 * ooz * x)
            yp = int(SCREEN_H / 2 - K1 * ooz * y * 0.5)

            luminance = cos_p * cos_t * sin_B - cos_A * cos_t * sin_p - sin_A * sin_t + cos_B * (cos_A * sin_t - cos_t * sin_A * sin_p)

            if 0 <= xp < SCREEN_W and 0 <= yp < SCREEN_H:
                idx = xp + SCREEN_W * yp
                if ooz > zbuffer[idx]:
                    zbuffer[idx] = ooz
                    L = int(luminance * 8)
                    output[idx] = SHADE[max(0, min(L, len(SHADE) - 1))]

            phi += PHI_STEP
        theta += THETA_STEP

    lines = []
    for row in range(SCREEN_H):
        start = row * SCREEN_W
        lines.append("".join(output[start:start + SCREEN_W]))
    return "\n".join(lines)

def main():
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    A = 0.0
    B = 0.0

    try:
        while True:
            frame = render_frame(A, B)
            sys.stdout.write("\033[H")
            sys.stdout.write(frame)
            sys.stdout.flush()

            A += 0.04
            B += 0.02
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

if __name__ == "__main__":
    main()