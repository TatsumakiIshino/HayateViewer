from __future__ import annotations
import os
import sys
import logging
from OpenGL.GL import *
from OpenGL.GL.shaders import compileShader, compileProgram

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class ShaderManager:
    """シェーダーのコンパイルと管理を担当するクラス。"""

    def __init__(self, settings_manager):
        self.settings_manager = settings_manager
        self.shader_program = None

    def load_shaders(self):
        """シェーダーをロードしてコンパイルする。"""
        if self.shader_program is not None:
            glDeleteProgram(self.shader_program)
        
        self.shader_program = self._create_shader_program()

    def _create_shader_program(self):
        try:
            vertex_shader_path = resource_path(os.path.join('app', 'shaders', 'vertex_shader.glsl'))
            
            resampling_mode = self.settings_manager.get('resampling_mode_gl', 'GL_LANCZOS3')
            shader_map = {
                'GL_NEAREST': 'nearest_fragment.glsl',
                'GL_BILINEAR': 'bilinear_fragment.glsl',
                'GL_LANCZOS3': 'lanczos3_fragment.glsl',
                'GL_LANCZOS4': 'lanczos4_fragment.glsl',
                'GL_QUINTIC': 'quintic_fragment.glsl',
            }
            fragment_shader_file = shader_map.get(resampling_mode, 'lanczos3_fragment.glsl')
            fragment_shader_path = resource_path(os.path.join('app', 'shaders', fragment_shader_file))

            if not os.path.exists(fragment_shader_path):
                logging.error(f"Fragment shader file not found: {fragment_shader_path}. Falling back to lanczos3.")
                fragment_shader_path = resource_path(os.path.join('app', 'shaders', 'lanczos3_fragment.glsl'))

            with open(vertex_shader_path, 'r', encoding='utf-8') as f:
                vertex_shader_source = f.read()
            with open(fragment_shader_path, 'r', encoding='utf-8') as f:
                fragment_shader_source = f.read()

            vs = compileShader(vertex_shader_source, GL_VERTEX_SHADER)
            fs = compileShader(fragment_shader_source, GL_FRAGMENT_SHADER)
            program = compileProgram(vs, fs)
            return program
        except Exception as e:
            logging.error(f"シェーダーのコンパイルに失敗: {e}", exc_info=True)
            return None

    def use_program(self):
        if self.shader_program is not None:
            glUseProgram(self.shader_program)

    def cleanup(self):
        if self.shader_program:
            glDeleteProgram(self.shader_program)
            self.shader_program = None
