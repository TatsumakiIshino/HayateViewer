#version 330 core

in vec2 TexCoord;
out vec4 FragColor;

uniform sampler2D textureSampler;
uniform vec2 sourceTextureSize;

const float PI = 3.14159265359;
const float LANCZOS_A = 3.0;

float lanczos(float x) {
    if (x == 0.0) {
        return 1.0;
    }
    if (abs(x) < LANCZOS_A) {
        return sin(PI * x) * sin(PI * x / LANCZOS_A) / (PI * PI * x * x / LANCZOS_A);
    }
    return 0.0;
}

void main() {
    vec2 texelSize = 1.0 / sourceTextureSize;
    vec4 color = vec4(0.0);
    float totalWeight = 0.0;

    for (float y = -LANCZOS_A + 0.5; y < LANCZOS_A; y += 1.0) {
        for (float x = -LANCZOS_A + 0.5; x < LANCZOS_A; x += 1.0) {
            vec2 offset = vec2(x, y);
            vec2 samplePos = TexCoord + offset * texelSize;
            
            if (samplePos.x >= 0.0 && samplePos.x <= 1.0 && samplePos.y >= 0.0 && samplePos.y <= 1.0) {
                float weightX = lanczos(x);
                float weightY = lanczos(y);
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