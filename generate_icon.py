"""
生成程序图标（Windows .ico 文件）
在 Windows 上运行：python generate_icon.py
"""
import struct
import io
import zlib


def create_simple_ico(filepath, size=32):
    """
    用纯 Python 生成一个简单的绿色盾牌图标 .ico 文件
    不需要任何第三方库
    """
    # 创建一个简单的 32x32 位图数据（BGRA 格式）
    width, height = size, size
    pixels = []

    for y in range(height):
        row = []
        for x in range(width):
            # 计算到中心点的距离
            cx, cy = width // 2, height // 2
            dx, dy = x - cx, y - cy
            dist = (dx * dx + dy * dy) ** 0.5

            # 盾牌形状判断
            in_shield = False
            # 主体：圆形但不是顶部和底部边缘
            if dist < cx - 1 and y > 4 and y < height - 4:
                in_shield = True
            # 矩形底部
            if 4 <= y <= height - 4 and 4 <= x <= width - 4:
                in_shield = True

            if in_shield:
                # 绿色
                r, g, b, a = 46, 204, 113, 255
            else:
                # 透明
                r, g, b, a = 0, 0, 0, 0

            # BGRA 格式
            row.extend([b, g, r, a])
        # 每行补齐到 4 字节边界
        row += [0] * ((4 - len(row) % 4) % 4)
        pixels.extend(row)

    # 构造 BMP 格式的像素数据（用于 ICO 的 AND/OR 掩码）
    # ICO 格式：
    # - ICO 头 (6 bytes)
    # - 目录项 (16 bytes)
    # - BMP 信息头 (40 bytes)
    # - 像素数据 (BGRA)

    # BMP 信息头
    bih = struct.pack(
        '<IiiHHIIiiII',
        40,           # 结构大小
        width,        # 宽
        height * 2,   # 高（BMP 中为 2倍，包含 AND 掩码）
        1,            # 颜色平面
        32,           # 位深
        0,            # 压缩
        len(pixels),  # 图像大小
        0, 0,         # 分辨率
        0,            # 调色板颜色数
        0             # 重要颜色数
    )

    # AND 掩码（透明通道掩码，全0 = 不透明）
    and_mask_size = ((width + 31) // 32) * 4 * height
    and_mask = b'\x00' * and_mask_size

    # 像素数据 + AND 掩码（BMP 中高度是 2*height，先放 XOR 再放 AND）
    # 注意：BMP 中像素行是倒序的
    xor_data = bytearray()
    for y in range(height - 1, -1, -1):
        start = y * width * 4
        row = pixels[start:start + width * 4]
        xor_data.extend(row)

    image_data = bytes(xor_data) + and_mask

    # ICO 目录项
    data_size = len(bih) + len(image_data)
    data_offset = 6 + 16  # 头 + 1个目录项

    header = struct.pack('<HHH', 0, 1, 1)  # 保留=0, 类型=1(ICO), 数量=1
    entry = struct.pack(
        '<BBBBHHII',
        width if width < 256 else 0,
        height if height < 256 else 0,
        0,        # 调色板颜色数
        0,        # 保留
        1,        # 颜色平面
        32,       # 位深
        data_size,
        data_offset
    )

    with open(filepath, 'wb') as f:
        f.write(header)
        f.write(entry)
        f.write(bih)
        f.write(image_data)

    print(f"Icon generated: {filepath} ({width}x{height})")


if __name__ == "__main__":
    import os
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
    create_simple_ico(output_path)
