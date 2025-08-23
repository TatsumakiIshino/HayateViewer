#version 330 core

in vec2 TexCoord;
out vec4 FragColor;

uniform sampler2D textureSampler;
uniform vec2 sourceTextureSize;

const float PI = 3.14159265359;

// 5次補間 (Quintic) のためのカーネル関数
// Keys, "Cubic-Convolution Interpolation for Digital Image Processing"
// を参考に、より高次の補間を実装
float quintic_weight(float x) {
    x = abs(x);
    float x2 = x * x;
    float x3 = x2 * x;
    if (x <= 1.0) {
        return (1.0/2.0) * x3 - x2 + (1.0/2.0) * x + 1.0;
    } else if (x <= 2.0) {
        return (-1.0/6.0) * x3 + x2 - (11.0/6.0) * x + 1.0;
    }
    return 0.0;
}


void main() {
    vec2 texelSize = 1.0 / sourceTextureSize;
    vec4 color = vec4(0.0);
    float totalWeight = 0.0;
    const float support = 2.0; // Quinticのサポート範囲

    for (float y = -support + 0.5; y < support; y += 1.0) {
        for (float x = -support + 0.5; x < support; x += 1.0) {
            vec2 offset = vec2(x, y);
            vec2 samplePos = TexCoord + offset * texelSize;

            if (samplePos.x >= 0.0 && samplePos.x <= 1.0 && samplePos.y >= 0.0 && samplePos.y <= 1.0) {
                float weightX = quintic_weight(x);
                float weightY = quintic_weight(y);
                float weight = weightX * weightY;

                color += texture(textureSampler, samplePos) * weight;
                totalWeight += weight;
            }
        }
    }

    if (totalWeight > 0.0) {
        FragColor = color / totalWeight;
    } else {
        FragColor = texture(textureSampler, TexCoord);
    }
}