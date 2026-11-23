package main

import "fmt"

func imagePromptInstructions(options object) string {
	base := "优化用户的生图提示词，保留原意、语言和引用对象。只输出可直接用于生成或编辑的提示词，不要解释你的推理。质量、分辨率、输出格式、背景和帧数由独立 API 字段控制，不要在提示词中重新设定这些技术参数。"
	switch objectString(options, "output_format") {
	case "svg":
		return base + "\n输出目标是静态 SVG 矢量图。将描述组织为【主体与轮廓】【路径与分层】【色彩与留白】【矢量约束】四个简短文本块，说明可编辑的几何形状、曲线、边缘、前后层次和图形之间的关系。保持图形简洁而准确，文字应可读，不依赖摄影噪点或位图纹理。只写设计提示词，不输出 SVG 代码、时间轴或动画要求。"
	case "gif":
		frames := 9
		if n, ok := tokenCount(options["frame_count"]); ok && n > 0 {
			frames = int(n)
		}
		return base + fmt.Sprintf("\n输出目标是 GIF 循环动图，共 %d 帧，帧间隔由接口固定控制。将提示词组织为【画面设定】【运动脚本】【分帧控制】【循环衔接】【稳定约束】五个简短文本块。明确主体动作、起始姿态、动作方向与幅度、运动节奏及关键阶段，描述连续动作而不是多格静态拼贴；主体身份、几何、配色、背景和机位应跨帧一致。让最后一帧自然接回第一帧，避免闪烁、跳变、瞬移和文字抖动。保留用户指定的镜头运动，只在用户需要时加入新的动态。", frames)
	}
	return base
}
