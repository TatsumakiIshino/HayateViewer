#version 330 core
out vec4 FragColor;

in vec2 TexCoord;

uniform sampler2D textureSampler;
uniform vec2 sourceTextureSize;
uniform vec2 targetResolution;

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

vec4 texture_lanczos(sampler2D tex, vec2 uv) {
    vec2 texel_size = 1.0 / sourceTextureSize;
    vec2 scaled_uv = uv * targetResolution / sourceTextureSize;
    vec2 center_texel = scaled_uv / texel_size;
    vec2 frac_texel = fract(center_texel);

    vec4 total_color = vec4(0.0);
    float total_weight = 0.0;

    for (float j = -LANCZOS_A + 1.0; j < LANCZOS_A; j++) {
        for (float i = -LANCZOS_A + 1.0; i < LANCZOS_A; i++) {
            vec2 offset = vec2(i, j);
            vec2 sample_pos = floor(center_texel) + offset;
            vec2 sample_uv = sample_pos * texel_size;

            float dist_x = abs(frac_texel.x - offset.x);
            float dist_y = abs(frac_texel.y - offset.y);
            float weight_x = lanczos(dist_x);
            float weight_y = lanczos(dist_y);
            float weight = weight_x * weight_y;

            if (weight > 0.0) {
                total_color += texture(tex, sample_uv) * weight;
                total_weight += weight;
            }
        }
    }

    if (total_weight > 0.0) {
        return total_color / total_weight;
    }
    return texture(tex, uv);
}

void main()
{
    FragColor = texture_lanczos(textureSampler, TexCoord);
}
