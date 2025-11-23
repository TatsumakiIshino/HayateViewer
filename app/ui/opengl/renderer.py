from __future__ import annotations
import logging
import ctypes
import numpy as np
from PIL import Image
from OpenGL.GL import *
from PySide6.QtCore import QPointF

class Renderer:
    """OpenGL描画ロジックを担当するクラス。"""

    def __init__(self, shader_manager, texture_manager):
        self.shader_manager = shader_manager
        self.texture_manager = texture_manager
        self.vao = None
        self.vbo = None
        self.debug_save_render = False
        self.clear_color = (0.0, 0.0, 0.0, 1.0)

    def set_clear_color(self, color):
        self.clear_color = color

    def initialize(self):
        """OpenGLリソース（VAO/VBO）の初期化。"""
        glClearColor(*self.clear_color)
        glEnable(GL_FRAMEBUFFER_SRGB)
        self.vao, self.vbo = self._create_quad_vbo()

    def render(self, view_width, view_height, device_pixel_ratio, display_keys, zoom_level, pan_offset: QPointF):
        """描画を実行する。"""
        # 削除待ちテクスチャのクリーンアップ
        self.texture_manager.delete_pending_textures()

        glClear(GL_COLOR_BUFFER_BIT)
        
        shader_program = self.shader_manager.shader_program
        if not display_keys or shader_program is None:
            return

        glUseProgram(shader_program)
        glBindVertexArray(self.vao)
        
        physical_width = int(view_width * device_pixel_ratio)
        physical_height = int(view_height * device_pixel_ratio)
        glViewport(0, 0, physical_width, physical_height)

        # テクスチャの準備確認
        images_to_render = [self.texture_manager.get_texture(key) for key in display_keys]
        if not all(img is not None for img in images_to_render):
            # まだ準備できていないテクスチャがある場合は描画しない（または一部だけ描画する？）
            # 元のロジックでは return していた
            glBindVertexArray(0)
            glUseProgram(0)
            return

        # 描画ロジック（単ページ・見開き）
        self._render_images(images_to_render, physical_width, physical_height, zoom_level, pan_offset)

        glBindVertexArray(0)
        glUseProgram(0)
        
        if self.debug_save_render:
            self._save_rendered_frame(view_width, view_height, device_pixel_ratio)
            self.debug_save_render = False

    def _render_images(self, images_to_render, view_w, view_h, zoom_level, pan_offset):
        if len(images_to_render) == 1:
            self._render_single_page(images_to_render[0], view_w, view_h, zoom_level, pan_offset)
        elif len(images_to_render) == 2:
            self._render_spread_page(images_to_render, view_w, view_h, zoom_level, pan_offset)

    def _render_single_page(self, img_data, view_w, view_h, zoom_level, pan_offset):
        img_w, img_h = img_data['width'], img_data['height']
        if view_w == 0 or view_h == 0 or img_w == 0 or img_h == 0: return

        view_aspect = view_w / view_h
        img_aspect = img_w / img_h

        if view_aspect > img_aspect:
            fit_scale = view_h / img_h
        else:
            fit_scale = view_w / img_w

        img_w_ndc = (img_w * fit_scale / view_w) * 2.0
        img_h_ndc = (img_h * fit_scale / view_h) * 2.0

        scale_matrix = np.array([[img_w_ndc / 2.0, 0, 0, 0], [0, img_h_ndc / 2.0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
        pan_x_ndc = (pan_offset.x() / view_w) * 2.0
        pan_y_ndc = -(pan_offset.y() / view_h) * 2.0
        trans_matrix = np.array([[1, 0, 0, pan_x_ndc], [0, 1, 0, pan_y_ndc], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
        zoom_matrix = np.array([[zoom_level, 0, 0, 0], [0, zoom_level, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
        
        transform_matrix = zoom_matrix @ trans_matrix @ scale_matrix
        self._draw_with_transform(img_data['texture_id'], img_w, img_h, transform_matrix)

    def _render_spread_page(self, images_to_render, view_w, view_h, zoom_level, pan_offset):
        total_img_w = sum(img['width'] for img in images_to_render)
        max_img_h = max(img['height'] for img in images_to_render)

        if view_w == 0 or view_h == 0 or total_img_w == 0 or max_img_h == 0: return

        view_aspect = view_w / view_h
        total_img_aspect = total_img_w / max_img_h

        if view_aspect > total_img_aspect:
            fit_scale = view_h / max_img_h
        else:
            fit_scale = view_w / total_img_w
        
        scaled_total_w_ndc = (total_img_w * fit_scale / view_w) * 2.0
        current_x_offset_ndc = -scaled_total_w_ndc / 2.0

        for img_data in images_to_render:
            img_w, img_h = img_data['width'], img_data['height']
            
            img_w_ndc = (img_w * fit_scale / view_w) * 2.0
            img_h_ndc = (img_h * fit_scale / view_h) * 2.0

            scale_matrix = np.array([[img_w_ndc / 2.0, 0, 0, 0], [0, img_h_ndc / 2.0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
            center_x_ndc = current_x_offset_ndc + (img_w_ndc / 2.0)
            pan_x_ndc = (pan_offset.x() / view_w) * 2.0
            pan_y_ndc = -(pan_offset.y() / view_h) * 2.0
            trans_matrix = np.array([[1, 0, 0, center_x_ndc + pan_x_ndc], [0, 1, 0, pan_y_ndc], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
            zoom_matrix = np.array([[zoom_level, 0, 0, 0], [0, zoom_level, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
            
            transform_matrix = zoom_matrix @ trans_matrix @ scale_matrix
            self._draw_with_transform(img_data['texture_id'], img_w, img_h, transform_matrix)
            current_x_offset_ndc += img_w_ndc

    def _draw_with_transform(self, texture_id, width, height, transform_matrix):
        if texture_id is None: return
            
        transform_loc = glGetUniformLocation(self.shader_manager.shader_program, "transform")
        glUniformMatrix4fv(transform_loc, 1, GL_TRUE, transform_matrix)

        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, texture_id)
        glUniform1i(glGetUniformLocation(self.shader_manager.shader_program, "textureSampler"), 0)
        glUniform2f(glGetUniformLocation(self.shader_manager.shader_program, "sourceTextureSize"), width, height)

        glDrawArrays(GL_TRIANGLE_FAN, 0, 4)

    def _create_quad_vbo(self):
        vertices = [
            # positions   # texture coords (Y flipped)
            -1.0,  1.0,  0.0, 0.0,
            -1.0, -1.0,  0.0, 1.0,
             1.0, -1.0,  1.0, 1.0,
             1.0,  1.0,  1.0, 0.0,
        ]
        vertices = np.array(vertices, dtype=np.float32)

        vao = glGenVertexArrays(1)
        vbo = glGenBuffers(1)
        glBindVertexArray(vao)
        glBindBuffer(GL_ARRAY_BUFFER, vbo)
        glBufferData(GL_ARRAY_BUFFER, vertices.nbytes, vertices, GL_STATIC_DRAW)
        
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * vertices.itemsize, ctypes.c_void_p(0))
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * vertices.itemsize, ctypes.c_void_p(2 * vertices.itemsize))
        glEnableVertexAttribArray(1)
        
        glBindBuffer(GL_ARRAY_BUFFER, 0)
        glBindVertexArray(0)
        return vao, vbo

    def _save_rendered_frame(self, w, h, ratio):
        try:
            physical_w = int(w * ratio)
            physical_h = int(h * ratio)
            data = glReadPixels(0, 0, physical_w, physical_h, GL_RGBA, GL_UNSIGNED_BYTE)
            
            image = Image.frombytes("RGBA", (physical_w, physical_h), data)
            image = image.transpose(Image.FLIP_TOP_BOTTOM)

            filename = "debug_output.png"
            image.save(filename)
            logging.info(f"Saved debug frame to {filename}")
        except Exception as e:
            logging.error(f"Failed to save rendered frame: {e}", exc_info=True)

    def cleanup(self):
        if self.vao:
            glDeleteVertexArrays(1, [self.vao])
        if self.vbo:
            glDeleteBuffers(1, [self.vbo])
