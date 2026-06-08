import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
from typing import List, Tuple
import threading
from datetime import datetime

import fitz  # PyMuPDF


# ==================== PDF处理类 ====================
class PDFProcessor:
    """PDF处理核心逻辑"""
    
    def __init__(self, input_paths: List[str], output_path: str, 
                 page_layout: Tuple[int, int], margin: float, 
                 paper_size: str, progress_callback, finish_callback, error_callback,
                 preserve_editability: bool = False, dpi: int = 300):
        self.input_paths = input_paths
        self.output_path = output_path
        self.cols, self.rows = page_layout
        self.margin = margin
        self.paper_size = paper_size
        self.progress_callback = progress_callback
        self.finish_callback = finish_callback
        self.error_callback = error_callback
        self.preserve_editability = preserve_editability
        self.dpi = dpi
    
    def get_page_size(self, size_name: str) -> Tuple[float, float]:
        """获取标准纸张尺寸（单位：点，1点=1/72英寸）"""
        sizes = {
            "A4": (595, 842),
            "A3": (842, 1191),
            "Letter": (612, 792),
            "Legal": (612, 1008),
            "A5": (420, 595),
        }
        return sizes.get(size_name, (595, 842))
    
    def copy_page_as_pixmap(self, src_doc, src_page_num, dest_page, dest_rect):
        """将页面渲染为高清图片后放置"""
        src_page = src_doc[src_page_num]
        
        src_rect = src_page.rect
        target_width = dest_rect.width
        target_height = dest_rect.height
        
        base_zoom_x = target_width / src_rect.width
        base_zoom_y = target_height / src_rect.height
        base_zoom = min(base_zoom_x, base_zoom_y)
        
        render_zoom = base_zoom * (self.dpi / 72)
        mat = fitz.Matrix(render_zoom, render_zoom)
        
        pix = src_page.get_pixmap(matrix=mat, alpha=False)
        
        img_width_pt = pix.width / (self.dpi / 72)
        img_height_pt = pix.height / (self.dpi / 72)
        
        x = dest_rect.x0 + (dest_rect.width - img_width_pt) / 2
        y = dest_rect.y0 + (dest_rect.height - img_height_pt) / 2
        
        dest_page.insert_image(fitz.Rect(x, y, x + img_width_pt, y + img_height_pt), pixmap=pix)
    
    def copy_page_with_all_content(self, src_doc, src_page_num, dest_page, dest_rect):
        """完整复制页面内容（尝试保留可编辑性）"""
        src_page = src_doc[src_page_num]
        dest_page.show_pdf_page(dest_rect, src_doc, src_page_num)
        
        src_rect = src_page.rect
        scale_x = dest_rect.width / src_rect.width
        scale_y = dest_rect.height / src_rect.height
        scale = min(scale_x, scale_y)
        
        scaled_width = src_rect.width * scale
        scaled_height = src_rect.height * scale
        offset_x = dest_rect.x0 + (dest_rect.width - scaled_width) / 2
        offset_y = dest_rect.y0 + (dest_rect.height - scaled_height) / 2
        
        annots = src_page.annots()
        if annots:
            for annot in annots:
                try:
                    annot_rect = annot.rect
                    new_rect = fitz.Rect(
                        offset_x + (annot_rect.x0 - src_rect.x0) * scale,
                        offset_y + (annot_rect.y0 - src_rect.y0) * scale,
                        offset_x + (annot_rect.x1 - src_rect.x0) * scale,
                        offset_y + (annot_rect.y1 - src_rect.y0) * scale
                    )
                    annot_xobj = annot.xobj
                    if annot_xobj:
                        dest_page.show_pdf_page(new_rect, src_doc, src_page_num, clip=annot_rect)
                    else:
                        annot_type = annot.type[1]
                        new_annot = dest_page.add_annot(annot_type, new_rect)
                        if annot.colors:
                            new_annot.set_colors(annot.colors.get())
                except Exception:
                    pass
        
        try:
            xobjects = src_page.get_images(full=True)
            for img in xobjects:
                xref = img[0]
                try:
                    img_rects = src_page.get_image_rects(xref)
                    for img_rect in img_rects:
                        new_rect = fitz.Rect(
                            offset_x + (img_rect.x0 - src_rect.x0) * scale,
                            offset_y + (img_rect.y0 - src_rect.y0) * scale,
                            offset_x + (img_rect.x1 - src_rect.x0) * scale,
                            offset_y + (img_rect.y1 - src_rect.y0) * scale
                        )
                        dest_page.insert_image(new_rect, xref=xref)
                except Exception:
                    pass
        except Exception:
            pass
    
    def run(self):
        """执行PDF拼版处理"""
        try:
            src_docs = []
            total_pages = 0
            
            for path in self.input_paths:
                if not os.path.exists(path):
                    self.error_callback(f"文件不存在: {path}")
                    return
                doc = fitz.open(path)
                src_docs.append(doc)
                total_pages += len(doc)
            
            if total_pages == 0:
                self.error_callback("没有找到任何页面")
                return
            
            dst_doc = fitz.open()
            paper_width, paper_height = self.get_page_size(self.paper_size)
            
            cells_per_page = self.cols * self.rows
            cell_width = (paper_width - self.margin * (self.cols + 1)) / self.cols
            cell_height = (paper_height - self.margin * (self.rows + 1)) / self.rows
            
            all_pages = []
            for doc in src_docs:
                for page_num in range(len(doc)):
                    all_pages.append((doc, page_num))
            
            # for idx, (src_doc, src_page_num) in enumerate(all_pages):
            #     if idx % cells_per_page == 0:
            #         current_dst_page = dst_doc.new_page(width=paper_width, height=paper_height)
            #         self.progress_callback(int(idx / total_pages * 100), f"处理第 {idx + 1}/{total_pages} 页")
            for idx, (src_doc, src_page_num) in enumerate(all_pages):
                if idx % cells_per_page == 0:
                    # 创建新页面
                    current_dst_page = dst_doc.new_page(width=paper_width, height=paper_height)
                    
                    # ========== 添加水平虚线（在页面中间）==========
                    # 计算页面中间位置的Y坐标
                    middle_y = paper_height / 2
                    # 设置虚线样式：线段长度3，间距3
                    current_dst_page.draw_line(
                        fitz.Point(0, middle_y),           # 起点 (左边缘, 中间Y)
                        fitz.Point(paper_width, middle_y), # 终点 (右边缘, 中间Y)
                        color=(0.5, 0.5, 0.5),             # 灰色 RGB (0-1范围)
                        width=0.5,                         # 线条宽度
                        dashes="[3 3] 0"                   # 虚线样式：3点实线，3点空白
                    )
                    # ============================================
                    
                    self.progress_callback(int(idx / total_pages * 100), f"处理第 {idx + 1}/{total_pages} 页")
                
                pos_in_page = idx % cells_per_page
                row = pos_in_page // self.cols
                col = pos_in_page % self.cols
                
                x = self.margin + col * (cell_width + self.margin)
                y = self.margin + row * (cell_height + self.margin)
                
                src_page = src_doc[src_page_num]
                src_rect = src_page.rect
                
                scale = min(cell_width / src_rect.width, cell_height / src_rect.height)
                scaled_width = src_rect.width * scale
                scaled_height = src_rect.height * scale
                
                offset_x = (cell_width - scaled_width) / 2
                offset_y = (cell_height - scaled_height) / 2
                
                dest_rect = fitz.Rect(
                    x + offset_x, 
                    y + offset_y,
                    x + offset_x + scaled_width,
                    y + offset_y + scaled_height
                )
                
                if self.preserve_editability:
                    self.copy_page_with_all_content(src_doc, src_page_num, current_dst_page, dest_rect)
                else:
                    self.copy_page_as_pixmap(src_doc, src_page_num, current_dst_page, dest_rect)
                
                self.progress_callback(int((idx + 1) / total_pages * 100), f"处理第 {idx + 1}/{total_pages} 页")
            
            self.progress_callback(100, "正在保存文件...")
            dst_doc.save(self.output_path, garbage=4, deflate=True)
            
            for doc in src_docs:
                doc.close()
            dst_doc.close()
            
            self.finish_callback(self.output_path)
            
        except Exception as e:
            self.error_callback(f"处理出错: {str(e)}")


# ==================== 支持拖放的文件列表控件 ====================
class DragDropListbox(tk.Frame):
    """支持拖放的文件列表控件"""
    
    def __init__(self, parent, callback_add_files, height=12, **kwargs):
        super().__init__(parent, **kwargs)
        self.callback_add_files = callback_add_files
        
        self.listbox = tk.Listbox(
            self,
            selectmode=tk.EXTENDED,
            activestyle='none',
            bg='#fafafa',
            fg='#333333',
            selectbackground='#4CAF50',
            selectforeground='white',
            relief=tk.FLAT,
            highlightthickness=2,
            highlightcolor='#4CAF50',
            highlightbackground='#cccccc',
            font=('微软雅黑', 10),
            height=height
        )
        
        scrollbar = tk.Scrollbar(self, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.setup_drag_drop()
    
    def setup_drag_drop(self):
        try:
            from tkinterdnd2 import DND_FILES
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind('<<Drop>>', self.on_drop)
            self.listbox.configure(bg='#fafafa')
        except ImportError:
            self.listbox.configure(bg='#f0f0f0')
            self.listbox.insert(0, "提示：安装 tkinterdnd2 可支持拖放")
            self.listbox.insert(1, "pip install tkinterdnd2")
            self.listbox.configure(fg='#999999')
    
    def on_drop(self, event):
        files = event.data
        if files:
            file_paths = []
            files = files.strip('{}')
            for f in files.split('} {') if '} {' in files else files.split():
                f = f.strip('{}').strip()
                if f.lower().endswith('.pdf') and os.path.exists(f):
                    file_paths.append(f)
            
            if file_paths and self.callback_add_files:
                self.callback_add_files(file_paths)
    
    def insert(self, index, *elements):
        self.listbox.insert(index, *elements)
    
    def delete(self, first, last=None):
        self.listbox.delete(first, last)
    
    def curselection(self):
        return self.listbox.curselection()
    
    def clear(self):
        self.listbox.delete(0, tk.END)


# ==================== 主窗口 ====================
class PDFMergerGUI:
    def __init__(self):
        try:
            from tkinterdnd2 import TkinterDnD
            self.root = TkinterDnD.Tk()
        except ImportError:
            self.root = tk.Tk()
        
        self.root.title("PDF拼版工具 - 高清发票专用（保留印章）")
        self.root.geometry("800x860")
        self.root.minsize(600, 860)
        
        self.input_files = []
        self.output_path = None
        self.processor_thread = None
        
        self.setup_styles()
        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def setup_styles(self):
        self.colors = {
            'bg': '#f0f0f0',
            'frame_bg': '#ffffff',
            'button_primary': '#4CAF50',
            'button_danger': '#f44336',
            'button_info': '#2196F3',
            'text': '#333333',
        }
        self.root.configure(bg=self.colors['bg'])
        self.fonts = {
            'title': ('微软雅黑', 12, 'bold'),
            'normal': ('微软雅黑', 10),
            'small': ('微软雅黑', 9)
        }
    
    def setup_ui(self):
        main_frame = tk.Frame(self.root, bg=self.colors['bg'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 文件列表区域
        file_frame = tk.LabelFrame(main_frame, text="PDF文件列表（拖放或点击添加）", 
                                    bg=self.colors['bg'], fg=self.colors['text'],
                                    font=self.fonts['title'])
        file_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        list_frame = tk.Frame(file_frame, bg=self.colors['bg'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.file_listbox = DragDropListbox(list_frame, self.add_files, height=12)
        self.file_listbox.pack(fill=tk.BOTH, expand=True)
        
        btn_frame = tk.Frame(file_frame, bg=self.colors['bg'])
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        add_btn = tk.Button(btn_frame, text="+ 添加文件", command=self.add_files_dialog,
                           bg=self.colors['button_primary'], fg='white',
                           font=self.fonts['normal'], cursor='hand2')
        add_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        remove_btn = tk.Button(btn_frame, text="- 移除选中", command=self.remove_selected,
                              bg=self.colors['button_danger'], fg='white',
                              font=self.fonts['normal'], cursor='hand2')
        remove_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        clear_btn = tk.Button(btn_frame, text="清空全部", command=self.clear_files,
                             bg=self.colors['button_danger'], fg='white',
                             font=self.fonts['normal'], cursor='hand2')
        clear_btn.pack(side=tk.LEFT)
        
        # 参数设置区域
        settings_frame = tk.LabelFrame(main_frame, text="拼版参数设置",
                                       bg=self.colors['bg'], fg=self.colors['text'],
                                       font=self.fonts['title'])
        settings_frame.pack(fill=tk.X, pady=(0, 10))
        
        settings_inner = tk.Frame(settings_frame, bg=self.colors['bg'])
        settings_inner.pack(fill=tk.X, padx=10, pady=10)
        
        # 纸张大小
        row = 0
        tk.Label(settings_inner, text="纸张大小:", bg=self.colors['bg'],
                font=self.fonts['normal']).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.paper_size_var = tk.StringVar(value="A4")
        paper_combo = ttk.Combobox(settings_inner, textvariable=self.paper_size_var,
                                   values=["A4", "A3", "Letter", "Legal", "A5"],
                                   state="readonly", width=10)
        paper_combo.grid(row=row, column=1, sticky=tk.W, pady=5, padx=(10, 0))
        
        # 布局设置
        row += 1
        tk.Label(settings_inner, text="每页布局:", bg=self.colors['bg'],
                font=self.fonts['normal']).grid(row=row, column=0, sticky=tk.W, pady=5)
        
        layout_frame = tk.Frame(settings_inner, bg=self.colors['bg'])
        layout_frame.grid(row=row, column=1, sticky=tk.W, pady=5, padx=(10, 0))
        
        tk.Label(layout_frame, text="列数:", bg=self.colors['bg'],
                font=self.fonts['normal']).pack(side=tk.LEFT)
        self.cols_var = tk.IntVar(value=1)
        cols_spin = tk.Spinbox(layout_frame, from_=1, to=4, width=3,
                               textvariable=self.cols_var,
                               font=self.fonts['normal'])
        cols_spin.pack(side=tk.LEFT, padx=(5, 15))
        
        tk.Label(layout_frame, text="行数:", bg=self.colors['bg'],
                font=self.fonts['normal']).pack(side=tk.LEFT)
        self.rows_var = tk.IntVar(value=2)
        rows_spin = tk.Spinbox(layout_frame, from_=1, to=4, width=3,
                               textvariable=self.rows_var,
                               font=self.fonts['normal'])
        rows_spin.pack(side=tk.LEFT, padx=(5, 0))
        
        # 边距
        row += 1
        tk.Label(settings_inner, text="页面边距:", bg=self.colors['bg'],
                font=self.fonts['normal']).grid(row=row, column=0, sticky=tk.W, pady=5)
        margin_frame = tk.Frame(settings_inner, bg=self.colors['bg'])
        margin_frame.grid(row=row, column=1, sticky=tk.W, pady=5, padx=(10, 0))
        
        self.margin_var = tk.DoubleVar(value=20)
        margin_spin = tk.Spinbox(margin_frame, from_=0, to=100, width=6,
                                 textvariable=self.margin_var,
                                 font=self.fonts['normal'])
        margin_spin.pack(side=tk.LEFT)
        tk.Label(margin_frame, text="点 (1点≈1/72英寸)", bg=self.colors['bg'],
                font=self.fonts['small']).pack(side=tk.LEFT, padx=(5, 0))
        
        # 快速预设按钮
        row += 1
        tk.Label(settings_inner, text="快速预设:", bg=self.colors['bg'],
                font=self.fonts['normal']).grid(row=row, column=0, sticky=tk.W, pady=5)
        
        preset_frame = tk.Frame(settings_inner, bg=self.colors['bg'])
        preset_frame.grid(row=row, column=1, sticky=tk.W, pady=5, padx=(10, 0))
        
        preset_1x2 = tk.Button(preset_frame, text="双联竖向（1列×2行）",
                              command=lambda: self.set_layout_preset(1, 2),
                              bg=self.colors['button_info'], fg='white',
                              font=self.fonts['small'], cursor='hand2')
        preset_1x2.pack(side=tk.LEFT, padx=(0, 5))
        
        preset_2x1 = tk.Button(preset_frame, text="双联横向（2列×1行）",
                              command=lambda: self.set_layout_preset(2, 1),
                              bg=self.colors['button_info'], fg='white',
                              font=self.fonts['small'], cursor='hand2')
        preset_2x1.pack(side=tk.LEFT, padx=(0, 5))
        
        preset_2x2 = tk.Button(preset_frame, text="四联（2列×2行）",
                              command=lambda: self.set_layout_preset(2, 2),
                              bg=self.colors['button_info'], fg='white',
                              font=self.fonts['small'], cursor='hand2')
        preset_2x2.pack(side=tk.LEFT, padx=(0, 5))
        
        preset_3x2 = tk.Button(preset_frame, text="六联（3列×2行）",
                              command=lambda: self.set_layout_preset(3, 2),
                              bg=self.colors['button_info'], fg='white',
                              font=self.fonts['small'], cursor='hand2')
        preset_3x2.pack(side=tk.LEFT)
        
        # 图片质量设置
        row += 1
        tk.Label(settings_inner, text="图片质量:", bg=self.colors['bg'],
                font=self.fonts['normal']).grid(row=row, column=0, sticky=tk.W, pady=5)
        
        quality_frame = tk.Frame(settings_inner, bg=self.colors['bg'])
        quality_frame.grid(row=row, column=1, sticky=tk.W, pady=5, padx=(10, 0))
        
        self.quality_var = tk.StringVar(value="high")
        quality_high = tk.Radiobutton(quality_frame, text="高清 (300 DPI，推荐)",
                                      variable=self.quality_var, value="high",
                                      bg=self.colors['bg'], font=self.fonts['small'])
        quality_high.pack(anchor=tk.W)
        
        quality_ultra = tk.Radiobutton(quality_frame, text="超清 (600 DPI，文件较大)",
                                       variable=self.quality_var, value="ultra",
                                       bg=self.colors['bg'], font=self.fonts['small'])
        quality_ultra.pack(anchor=tk.W)
        
        quality_normal = tk.Radiobutton(quality_frame, text="标准 (150 DPI，文件较小)",
                                        variable=self.quality_var, value="normal",
                                        bg=self.colors['bg'], font=self.fonts['small'])
        quality_normal.pack(anchor=tk.W)
        
        # 处理模式选择
        row += 1
        tk.Label(settings_inner, text="处理模式:", bg=self.colors['bg'],
                font=self.fonts['normal']).grid(row=row, column=0, sticky=tk.W, pady=5)
        
        mode_frame = tk.Frame(settings_inner, bg=self.colors['bg'])
        mode_frame.grid(row=row, column=1, sticky=tk.W, pady=5, padx=(10, 0))
        
        self.mode_var = tk.StringVar(value="image")
        mode_image = tk.Radiobutton(mode_frame, text="图片模式（100%保留印章，文字变图片）",
                                    variable=self.mode_var, value="image",
                                    bg=self.colors['bg'], font=self.fonts['small'])
        mode_image.pack(anchor=tk.W)
        
        mode_edit = tk.Radiobutton(mode_frame, text="可编辑模式（保留文字可编辑，印章可能丢失）",
                                   variable=self.mode_var, value="edit",
                                   bg=self.colors['bg'], font=self.fonts['small'])
        mode_edit.pack(anchor=tk.W)
        
        # 进度条区域
        progress_frame = tk.LabelFrame(main_frame, text="处理进度",
                                       bg=self.colors['bg'], fg=self.colors['text'],
                                       font=self.fonts['title'])
        progress_frame.pack(fill=tk.X, pady=(0, 10))
        
        progress_inner = tk.Frame(progress_frame, bg=self.colors['bg'])
        progress_inner.pack(fill=tk.X, padx=10, pady=10)
        
        self.progress_bar = ttk.Progressbar(progress_inner, mode='determinate')
        self.progress_bar.pack(fill=tk.X)
        
        self.progress_label = tk.Label(progress_inner, text="就绪", 
                                       bg=self.colors['bg'], fg=self.colors['text'],
                                       font=self.fonts['small'])
        self.progress_label.pack(pady=(5, 0))
        
        # 操作按钮
        action_frame = tk.Frame(main_frame, bg=self.colors['bg'])
        action_frame.pack(fill=tk.X)
        
        self.output_btn = tk.Button(action_frame, text="📁 选择输出文件",
                                   command=self.select_output,
                                   bg=self.colors['button_primary'], fg='white',
                                   font=self.fonts['normal'], cursor='hand2')
        self.output_btn.pack(side=tk.LEFT)
        
        self.start_btn = tk.Button(action_frame, text="▶ 开始拼版",
                                  command=self.start_process,
                                  bg=self.colors['button_info'], fg='white',
                                  font=('微软雅黑', 12, 'bold'),
                                  cursor='hand2', state=tk.DISABLED)
        self.start_btn.pack(side=tk.RIGHT)
        
        # 状态栏
        status_frame = tk.Frame(self.root, bg='#e0e0e0')
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.status_bar = tk.Label(status_frame, text="就绪", 
                                   bg='#e0e0e0', fg=self.colors['text'],
                                   font=self.fonts['small'], anchor=tk.W)
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=2)
        
        info_label = tk.Label(status_frame, text="💡 提示：高清图片模式可100%保留发票印章", 
                              bg='#e0e0e0', fg='#2196F3',
                              font=self.fonts['small'], anchor=tk.W)
        info_label.pack(side=tk.RIGHT, padx=5, pady=2)
    
    def set_layout_preset(self, cols: int, rows: int):
        self.cols_var.set(cols)
        self.rows_var.set(rows)
    
    def add_files(self, files: List[str]):
        for file in files:
            if file not in self.input_files:
                self.input_files.append(file)
                self.file_listbox.insert(tk.END, os.path.basename(file))
        # 如果是第一次添加文件，自动设置默认输出路径
        if not self.output_path and self.input_files:
            self.set_default_output_path()
        self.update_start_button()
        self.status_bar.config(text=f"已添加 {len(files)} 个文件，共 {len(self.input_files)} 个")
    
    def add_files_dialog(self):
        files = filedialog.askopenfilenames(
            title="选择PDF文件",
            filetypes=[("PDF文件", "*.pdf"), ("所有文件", "*.*")]
        )
        if files:
            self.add_files(list(files))
    
    def remove_selected(self):
        selected = self.file_listbox.curselection()
        for idx in reversed(selected):
            self.file_listbox.delete(idx)
            del self.input_files[idx]
        # 如果删除了所有文件，重置输出路径
        if not self.input_files:
            self.output_path = None
            self.output_btn.config(text="📁 选择输出文件")
        self.update_start_button()
        self.status_bar.config(text=f"已移除，剩余 {len(self.input_files)} 个文件")
    
    def clear_files(self):
        self.file_listbox.clear()
        self.input_files.clear()
        self.output_path = None
        self.output_btn.config(text="📁 选择输出文件")
        self.update_start_button()
        self.status_bar.config(text="已清空文件列表")

    def set_default_output_path(self):
        """设置默认输出路径：第一个PDF所在目录 + 合并+日期.pdf"""
        if not self.input_files:
            return
        first_file = self.input_files[0]
        dir_path = os.path.dirname(first_file)
        date_str = datetime.now().strftime("%Y%m%d")
        default_name = f"合并{date_str}.pdf"
        self.output_path = os.path.join(dir_path, default_name)
        self.output_btn.config(text=f"📁 {default_name}")
        self.update_start_button()
    
    def select_output(self):
        # 设置初始目录为第一个PDF文件所在位置
        initial_dir = None
        if self.input_files:
            initial_dir = os.path.dirname(self.input_files[0])

        path = filedialog.asksaveasfilename(
            title="保存PDF文件",
            defaultextension=".pdf",
            initialdir=initial_dir,
            initialfile=os.path.basename(self.output_path) if self.output_path else f"合并{datetime.now().strftime('%Y%m%d')}.pdf",
            filetypes=[("PDF文件", "*.pdf"), ("所有文件", "*.*")]
        )
        if path:
            self.output_path = path
            self.output_btn.config(text=f"📁 {os.path.basename(path)}")
            self.update_start_button()
            self.status_bar.config(text=f"输出文件: {path}")
    
    def update_start_button(self):
        if len(self.input_files) > 0 and self.output_path:
            self.start_btn.config(state=tk.NORMAL)
        else:
            self.start_btn.config(state=tk.DISABLED)
    
    def update_progress(self, value: int, message: str):
        self.progress_bar['value'] = value
        self.progress_label.config(text=message)
        self.status_bar.config(text=message)
        self.root.update_idletasks()
    
    def process_finished(self, output_path: str):
        self.start_btn.config(state=tk.NORMAL, text="▶ 开始拼版")
        self.output_btn.config(state=tk.NORMAL)
        self.progress_bar['value'] = 100
        self.progress_label.config(text="完成！")
        
        result = messagebox.askyesno(
            "完成",
            f"PDF拼版完成！\n\n输出文件：{output_path}\n\n是否打开所在文件夹？"
        )
        
        if result:
            os.startfile(os.path.dirname(output_path))
        
        self.status_bar.config(text=f"完成！文件已保存至: {output_path}")
        self.processor_thread = None
    
    def process_error(self, error_msg: str):
        self.start_btn.config(state=tk.NORMAL, text="▶ 开始拼版")
        self.output_btn.config(state=tk.NORMAL)
        self.progress_label.config(text="出错")
        messagebox.showerror("错误", error_msg)
        self.status_bar.config(text=f"错误: {error_msg}")
        self.processor_thread = None
    
    def start_process(self):
        if not self.input_files:
            messagebox.showwarning("警告", "请先添加PDF文件")
            return
        if not self.output_path:
            messagebox.showwarning("警告", "请先选择输出文件路径")
            return
        
        self.start_btn.config(state=tk.DISABLED, text="处理中...")
        self.output_btn.config(state=tk.DISABLED)
        self.progress_bar['value'] = 0
        
        page_layout = (self.cols_var.get(), self.rows_var.get())
        margin = self.margin_var.get()
        paper_size = self.paper_size_var.get()
        
        preserve_editability = (self.mode_var.get() == "edit")
        
        quality = self.quality_var.get()
        if quality == "high":
            dpi = 300
        elif quality == "ultra":
            dpi = 600
        else:
            dpi = 150
        
        self.processor = PDFProcessor(
            self.input_files, self.output_path, page_layout, margin, paper_size,
            self.update_progress, self.process_finished, self.process_error,
            preserve_editability, dpi
        )
        
        self.processor_thread = threading.Thread(target=self.processor.run)
        self.processor_thread.daemon = True
        self.processor_thread.start()
    
    def on_closing(self):
        if self.processor_thread and self.processor_thread.is_alive():
            result = messagebox.askyesno("确认", "正在处理中，确定要退出吗？")
            if not result:
                return
        self.root.destroy()


def main():
    try:
        import fitz
        print(f"PyMuPDF版本可用")
    except ImportError:
        print("错误：未安装PyMuPDF库")
        print("请运行: pip install PyMuPDF")
        return
    
    app = PDFMergerGUI()
    app.root.mainloop()


if __name__ == "__main__":
    main()
