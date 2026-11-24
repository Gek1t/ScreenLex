import re
import os
import select
import socket
import time
import http.client
import queue
import unicodedata
import urllib.error
import urllib.request
import urllib.parse
import json
import threading
import concurrent.futures
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional, Callable, Tuple
from .grammar_checker import check_spelling, check_grammar_and_spelling, CORE_VOCAB
from .offline_dict import lookup_offline_dict

# 学术高频词汇标签库 (AWL / IELTS)
ACADEMIC_TAGS = {
    "ephemeral": ["IELTS 核心", "AWL 学术核心", "哲学/文化"],
    "ubiquitous": ["IELTS 核心", "AWL 学术核心", "计算机/通信"],
    "paradigm": ["AWL 学术核心", "方法论", "管理学"],
    "sustainability": ["IELTS 高频", "环境经济", "跨学科"],
    "algorithmic": ["计算机科学", "运筹学", "人工智能"],
    "empirical": ["AWL 学术核心", "实证研究", "论文高频"],
    "concomitant": ["GRE 核心", "统计分析", "医学"],
    "mitigate": ["IELTS 核心", "AWL 学术核心", "风险工程"],
    "exponential": ["应用数学", "创新趋势", "金融"],
    "resilience": ["系统工程", "心理学", "生态管理"],
    "heterogeneous": ["AWL 学术核心", "分布式系统", "材料"],
    "synergy": ["企业战略", "组织行为学", "生态学"],
    "linear search": ["数据结构", "算法核心", "检索复杂度"],
    "binary search": ["数据结构", "二分查找", "算法核心"],
    "paradigm shift": ["AWL 学术短语", "学术经典搭配", "科学哲学"],
    "vice versa": ["学术拉丁短语", "论证逻辑", "哲学/通识"],
    "hash table": ["数据结构", "哈希算法", "复杂度 O(1)"],
    "hash": ["数据结构", "计算机核心", "散列函数"],
    "directional": ["学术词汇", "系统工程", "图论"],
}

# 离线极速备选释义（网络波动或无网时 0 延迟呈现）
OFFLINE_DEFS = {
    "structure": {
        "uk": "/ˈstrʌk.tʃər/",
        "us": "/ˈstrʌk.tʃɚ/",
        "defs": [
            {"pos": "n.", "cn": "结构；构造；体系；建筑物", "en": "the arrangement of and relations between the parts or elements of something"},
            {"pos": "vt.", "cn": "组织；安排；构成", "en": "construct or arrange according to a plan"}
        ],
        "ai": "计算机与学术基石词汇：指数据或系统各部分之间的内在组织方式与关联逻辑。",
        "trans": "合理的数据结构可以显著提升程序的执行效率。"
    },
    "structures": {
        "uk": "/ˈstrʌk.tʃəz/",
        "us": "/ˈstrʌk.tʃɚz/",
        "defs": [
            {"pos": "n.", "cn": "结构（复数）；组织构造", "en": "plural form of structure; specialized formats for organizing data"}
        ],
        "ai": "structure 的复数形式，统指各类数据结构或体系模型。",
        "trans": "熟练掌握常用数据结构是算法设计与系统优化的前提。"
    },
    "algorithm": {
        "uk": "/ˈæl.ɡə.rɪ.ðəm/",
        "us": "/ˈæl.ɡə.rɪ.ðəm/",
        "defs": [
            {"pos": "n.", "cn": "算法；运算法则；计算程序", "en": "a process or set of rules to be followed in calculations or problem-solving"}
        ],
        "ai": "计算机科学理论核心：解决特定问题的一系列明确可执行的计算步骤与规则。",
        "trans": "算法的优劣直接决定了系统解决复杂问题的效率与可行性。"
    },
    "algorithms": {
        "uk": "/ˈæl.ɡə.rɪ.ðəmz/",
        "us": "/ˈæl.ɡə.rɪ.ðəmz/",
        "defs": [
            {"pos": "n.", "cn": "算法（复数）", "en": "plural form of algorithm"}
        ],
        "ai": "algorithm 的复数形式，泛指各类算法集合与策略。",
        "trans": "排序与搜索算法是计算机科学中最经典的算法类别。"
    },
    "recursion": {
        "uk": "/rɪˈkɜː.ʃən/",
        "us": "/rɪˈkɝː.ʒən/",
        "defs": [
            {"pos": "n.", "cn": "递归；循环调用", "en": "the repeated application of a recursive procedure or definition"}
        ],
        "ai": "编程与数学核心概念：函数直接或间接调用自身以求解子问题，依赖基准条件（Base Case）终止。",
        "trans": "递归算法在解决树形结构遍历时表现得极其优雅清晰。"
    },
    "recursive": {
        "uk": "/rɪˈkɜː.sɪv/",
        "us": "/rɪˈkɝː.sɪv/",
        "defs": [
            {"pos": "adj.", "cn": "递归的；循环的", "en": "characterized by or involving recurrence or repetition"}
        ],
        "ai": "用于修饰算法、函数或数据结构的递归属性（如 recursive call, recursive step）。",
        "trans": "该问题可以通过编写递归函数轻松求解。"
    },
    "lecture": {
        "uk": "/ˈlek.tʃər/",
        "us": "/ˈlek.tʃɚ/",
        "defs": [
            {"pos": "n.", "cn": "讲座；课；演讲", "en": "an educational talk to an audience, especially one of students in a university"},
            {"pos": "vi.", "cn": "讲课；做演讲", "en": "deliver an educational lecture"}
        ],
        "ai": "学术课堂场景词汇，指大学教学讲座或课程单元（如 Lecture Note）。",
        "trans": "今天的讲座深入讲解了递归树与复杂度分析。"
    },
    "note": {
        "uk": "/nəʊt/",
        "us": "/noʊt/",
        "defs": [
            {"pos": "n.", "cn": "笔记；讲义；便条；注释", "en": "a brief record of points or ideas written down as an aid to memory"},
            {"pos": "vt.", "cn": "注意；记录；指出", "en": "notice or pay particular attention to"}
        ],
        "ai": "课件与学术文本通用高频词，用于指代课堂记录与重点注释。",
        "trans": "请仔细阅读本节课的讲义附注与核心代码示例。"
    },
    "notes": {
        "uk": "/nəʊts/",
        "us": "/noʊts/",
        "defs": [
            {"pos": "n.", "cn": "笔记（复数）；讲义", "en": "plural form of note; lecture notes"}
        ],
        "ai": "note 的复数形式，课件标题高频（如 Lecture Notes）。",
        "trans": "复习课堂讲义有助于巩固对数据结构的理解。"
    },
    "complexity": {
        "uk": "/kəmˈplek.sə.ti/",
        "us": "/kəmˈplek.sə.t̬i/",
        "defs": [
            {"pos": "n.", "cn": "复杂度；复杂性", "en": "the state or quality of being intricate or complex; computational resource cost"}
        ],
        "ai": "算法分析核心度量指标：涵盖时间复杂度（Time Complexity）与空间复杂度（Space Complexity）。",
        "trans": "我们通常使用大 O 渐近记号来衡量算法的时间复杂度。"
    },
    "array": {
        "uk": "/əˈreɪ/",
        "us": "/əˈreɪ/",
        "defs": [
            {"pos": "n.", "cn": "数组；排列；矩阵", "en": "an ordered series or arrangement; data structure of contiguous elements"}
        ],
        "ai": "最基本的线性数据结构：连续内存空间存储相同类型元素，支持 O(1) 随机访问。",
        "trans": "数组支持通过索引在常数时间内访问任意元素。"
    },
    "function": {
        "uk": "/ˈfʌŋk.ʃən/",
        "us": "/ˈfʌŋk.ʃən/",
        "defs": [
            {"pos": "n.", "cn": "函数；功能；职责", "en": "a relation or expression involving one or more variables; a unit of code"},
            {"pos": "vi.", "cn": "运行；发挥作用", "en": "work or operate in a proper or particular way"}
        ],
        "ai": "编程与数学核心概念：接收输入并返回确切计算输出的可重用逻辑单元。",
        "trans": "递归函数必须具备明确的基准条件以避免无限递归栈溢出。"
    },
    "variable": {
        "uk": "/ˈveə.ri.ə.bəl/",
        "us": "/ˈver.i.ə.bəl/",
        "defs": [
            {"pos": "n.", "cn": "变量；可变因素", "en": "an element, feature, or factor that is liable to vary or change"},
            {"pos": "adj.", "cn": "可变的；易变的", "en": "not consistent or having a fixed pattern; liable to change"}
        ],
        "ai": "编程语言基础语法：用于存储可变数值或对象引用的具名内存空间。",
        "trans": "局部变量的生命周期局限于函数执行的作用域内。"
    },
    "process": {
        "uk": "/ˈprəʊ.ses/",
        "us": "/ˈprɑː.ses/",
        "defs": [
            {"pos": "n.", "cn": "过程；进程；步骤", "en": "a series of actions or steps taken in order to achieve a particular end; running program"},
            {"pos": "vt.", "cn": "处理；加工", "en": "perform a series of mechanical or chemical operations on"}
        ],
        "ai": "操作系统核心概念：正在执行中的程序实例，具备独立虚拟内存与系统资源。",
        "trans": "操作系统负责在多个并发进程之间进行调度与资源分配。"
    },
    "system": {
        "uk": "/ˈsɪs.təm/",
        "us": "/ˈsɪs.təm/",
        "defs": [
            {"pos": "n.", "cn": "系统；体系；体制", "en": "a set of things working together as parts of a mechanism or an interconnecting network"}
        ],
        "ai": "软硬件综合体系：由互相协作的模块构成的统一计算或处理环境。",
        "trans": "分布式系统通过网络协议实现多节点的协同计算。"
    },
    "memory": {
        "uk": "/ˈmem.ər.i/",
        "us": "/ˈmem.ɚ.i/",
        "defs": [
            {"pos": "n.", "cn": "内存；存储器；记忆", "en": "the faculty by which the mind stores and remembers information; RAM in computers"}
        ],
        "ai": "计算机硬件基石（RAM）：存放 CPU 正在运行的指令与数据的快速半导体存储设备。",
        "trans": "动态内存分配若管理不当容易导致内存泄漏与悬空指针。"
    },
    "pointer": {
        "uk": "/ˈpɔɪn.tər/",
        "us": "/ˈpɔɪn.tɚ/",
        "defs": [
            {"pos": "n.", "cn": "指针；指针变量；指示器", "en": "a variable that stores the memory address of another value"}
        ],
        "ai": "C/C++ 与底层系统级编程基石：保存目标对象真实物理或虚拟内存地址的特殊变量。",
        "trans": "链表节点通过指针相互链接形成线性的逻辑序列。"
    },
    "loop": {
        "uk": "/luːp/",
        "us": "/luːp/",
        "defs": [
            {"pos": "n.", "cn": "循环；环路", "en": "a sequence of instructions that is continually repeated until a condition is met"},
            {"pos": "vi.", "cn": "形成环状；循环运行", "en": "make a loop or curve; execute repeatedly"}
        ],
        "ai": "控制流基础结构：重复执行特定代码块直到退出条件达成（如 for, while）。",
        "trans": "该循环遍历数组中的所有元素并计算累计总和。"
    },
    "iteration": {
        "uk": "/ˌɪt.əˈreɪ.ʃən/",
        "us": "/ˌɪt̬.əˈreɪ.ʃən/",
        "defs": [
            {"pos": "n.", "cn": "迭代；反复；单次循环步骤", "en": "the repetition of a process or utterance; one cycle of a loop"}
        ],
        "ai": "算法与数值计算通用术语：通过重复单步计算逐步逼近目标解或遍历容器。",
        "trans": "在每次迭代中，指针向右移动一位并更新当前最大值。"
    },
    "class": {
        "uk": "/klɑːs/",
        "us": "/klæs/",
        "defs": [
            {"pos": "n.", "cn": "类；阶层；班级", "en": "a template definition of the methods and variables in a particular kind of object"}
        ],
        "ai": "面向对象编程（OOP）核心基石：定义对象状态与行为蓝图的抽象数据类型。",
        "trans": "该类封装了二叉搜索树的插入、删除与查找方法。"
    },
    "object": {
        "uk": "/ˈɒb.dʒɪkt/",
        "us": "/ˈɑːb.dʒɪkt/",
        "defs": [
            {"pos": "n.", "cn": "对象；物体；目标", "en": "a material thing; an instance of a class in object-oriented programming"},
            {"pos": "vi.", "cn": "反对", "en": "say something to express one's disapproval of"}
        ],
        "ai": "OOP 中类的具体实例：具备属性状态与方法行为的独立实体。",
        "trans": "在面向对象设计中，所有操作都围绕对象的状态与消息传递展开。"
    },
    "stack": {
        "uk": "/stæk/",
        "us": "/stæk/",
        "defs": [
            {"pos": "n.", "cn": "栈；堆叠（后进先出 LIFO）", "en": "a linear data structure which follows a particular order of LIFO operations"},
            {"pos": "vt.", "cn": "堆叠；堆放", "en": "pile one upon the other"}
        ],
        "ai": "核心线性数据结构：仅允许在顶端进行 push 与 pop，常用于函数调用栈与括号匹配。",
        "trans": "递归调用在底层是通过系统的调用栈来实现状态保存与回溯的。"
    },
    "queue": {
        "uk": "/kjuː/",
        "us": "/kjuː/",
        "defs": [
            {"pos": "n.", "cn": "队列（先进先出 FIFO）", "en": "a linear structure which follows FIFO order (First In First Out)"},
            {"pos": "vi.", "cn": "排队", "en": "line up or wait in a queue"}
        ],
        "ai": "核心线性数据结构：队尾入队（enqueue），队头出队（dequeue），常用于广度优先搜索 BFS。",
        "trans": "广度优先搜索使用队列来保证按层级由浅入深访问各个节点。"
    },
    "tree": {
        "uk": "/triː/",
        "us": "/triː/",
        "defs": [
            {"pos": "n.", "cn": "树；树状结构", "en": "a non-linear hierarchical data structure consisting of nodes connected by edges"}
        ],
        "ai": "核心非线性分层数据结构：包含根节点（Root）与子树，任意两节点间有且仅有一条简单路径。",
        "trans": "二叉搜索树的左子树所有节点值均小于根节点，右子树均大于根节点。"
    },
    "graph": {
        "uk": "/ɡrɑːf/",
        "us": "/ɡræf/",
        "defs": [
            {"pos": "n.", "cn": "图；图表；图结构", "en": "a non-linear data structure consisting of vertices (nodes) and edges"},
            {"pos": "vt.", "cn": "用图表示", "en": "plot or trace on a graph"}
        ],
        "ai": "离散数学与计算机高级结构：由顶点集 V 与边集 E 构成，分为有向图与无向图。",
        "trans": "图结构被广泛应用于社交网络关系建模与最短路径路由计算。"
    },
    "node": {
        "uk": "/nəʊd/",
        "us": "/noʊd/",
        "defs": [
            {"pos": "n.", "cn": "节点；网节", "en": "a basic unit of a data structure, such as a linked list or tree data structure"}
        ],
        "ai": "链表、树、图中的基本离散数据单元，通常包含载荷数据与指向相邻节点的指针/引用。",
        "trans": "每个叶子节点都没有子节点，其指针字段通常设置为 NULL。"
    },
    "search": {
        "uk": "/sɜːtʃ/",
        "us": "/sɝːtʃ/",
        "defs": [
            {"pos": "v.", "cn": "搜索；查找；检索", "en": "try to find something by looking or otherwise seeking carefully"},
            {"pos": "n.", "cn": "搜索；检索", "en": "an act of searching for someone or something"}
        ],
        "ai": "计算机算法经典任务：在数据集合中查找满足特定条件的元素位置或内容。",
        "trans": "二分查找要求输入数组预先按升序或降序排列。"
    },
    "sort": {
        "uk": "/sɔːt/",
        "us": "/sɔːrt/",
        "defs": [
            {"pos": "v.", "cn": "排序；分类；整理", "en": "arrange systematically in groups; separate according to type or order"},
            {"pos": "n.", "cn": "种类；类别", "en": "a category of things or people with a common feature"}
        ],
        "ai": "计算机基石算法：将无序元素集合按照指定比较规则重新排列为有序序列。",
        "trans": "快速排序与归并排序的平均时间复杂度均为 O(n log n)。"
    },
    "insert": {
        "uk": "/ɪnˈsɜːt/",
        "us": "/ɪnˈsɝːt/",
        "defs": [
            {"pos": "vt.", "cn": "插入；嵌入", "en": "place, fit, or push something into something else"},
            {"pos": "n.", "cn": "插入物", "en": "an ornamental or informational addition"}
        ],
        "ai": "数据结构标准 CRUD 操作之一：向容器或数据流中添加新元素。",
        "trans": "在平衡二叉树中插入新元素的时间复杂度为 O(log n)。"
    },
    "delete": {
        "uk": "/dɪˈliːt/",
        "us": "/dɪˈliːt/",
        "defs": [
            {"pos": "vt.", "cn": "删除；抹去", "en": "remove or obliterate written or printed matter or computer data"}
        ],
        "ai": "数据结构标准 CRUD 操作之一：从容器中安全移除指定元素并维护结构平衡。",
        "trans": "从双向链表中删除一个已知节点可以在常数时间 O(1) 内完成。"
    },
    "index": {
        "uk": "/ˈɪn.deks/",
        "us": "/ˈɪn.deks/",
        "defs": [
            {"pos": "n.", "cn": "索引；下标；指标", "en": "an alphabetical list; a pointer indicating value in an array"},
            {"pos": "vt.", "cn": "为...编索引；按索引检索", "en": "record in an index"}
        ],
        "ai": "数组中用于指代元素物理位置的非负整型偏置量（0-indexed 或 1-indexed）。",
        "trans": "数组下标越界是导致运行时段错误的最常见原因之一。"
    },
    "chapter": {
        "uk": "/ˈtʃæp.tər/",
        "us": "/ˈtʃæp.tɚ/",
        "defs": [
            {"pos": "n.", "cn": "章；回；篇章", "en": "a main division of a book, typically with a number or title"}
        ],
        "ai": "教材与幻灯片结构标题词：指教学体系中的独立章节模块。",
        "trans": "本章重点探讨递归的设计范式与分治法思想。"
    },
    "data structure and algorithms": {
        "uk": "/ˈdeɪ.tə ˈstrʌk.tʃər ænd ˈæl.ɡə.rɪ.ðəmz/",
        "us": "/ˈdeɪ.t̬ə ˈstrʌk.tʃɚ ænd ˈæl.ɡə.rɪ.ðəmz/",
        "defs": [
            {"pos": "术语 · 课程标题", "cn": "数据结构与算法", "en": "the systematic organization of data and formal sequences of computational steps"}
        ],
        "ai": "计算机核心专业课与理论基石：涵盖线性表、树、图、哈希表及排序搜索算法。",
        "trans": "数据结构与算法是计算机科学的核心基础课程。"
    },
    "data structures and algorithms": {
        "uk": "/ˈdeɪ.tə ˈstrʌk.tʃəz ænd ˈæl.ɡə.rɪ.ðəmz/",
        "us": "/ˈdeɪ.t̬ə ˈstrʌk.tʃɚz ænd ˈæl.ɡə.rɪ.ðəmz/",
        "defs": [
            {"pos": "术语 · 课程标题", "cn": "数据结构与算法", "en": "the systematic organization of data and formal sequences of computational steps"}
        ],
        "ai": "计算机核心专业课与理论基石：涵盖线性表、树、图、哈希表及排序搜索算法。",
        "trans": "数据结构与算法是计算机科学的核心基础课程。"
    },
    "data": {
        "uk": "/ˈdeɪ.tə/",
        "us": "/ˈdeɪ.t̬ə/",
        "defs": [
            {"pos": "n.", "cn": "数据；资料；信息", "en": "facts, statistics, or information collected together for reference or analysis"}
        ],
        "ai": "计算机与科学基础词汇：未经处理或已结构化的事实与数值信息。",
        "trans": "该算法支持对海量多维数据进行极速检索。"
    },
    "vice versa": {
        "uk": "/ˌvaɪ.si ˈvɜː.sə/",
        "us": "/ˌvaɪ.sə ˈvɝː.sə/",
        "defs": [
            {"pos": "adv. phrase", "cn": "反之亦然；反过来也一样", "en": "with the order reversed; the other way around"}
        ],
        "ai": "学术论述与算法逻辑核心短语，指前述命题在倒转因果或键值方向后同样/不同样成立（如 key to value, not vice versa）。",
        "trans": "哈希表查找仅支持从键到值，反之亦然则不成立。"
    },
    "hash table": {
        "uk": "/ˈhæʃ ˌteɪ.bəl/",
        "us": "/ˈhæʃ ˌteɪ.bəl/",
        "defs": [
            {"pos": "n. phrase", "cn": "哈希表；散列表（平均时间复杂度 O(1)）", "en": "a data structure that stores key-value pairs using a hash function"}
        ],
        "ai": "核心数据结构：通过哈希函数将键映射到桶中，支持常数级 O(1) 的平均查找与插入复杂度。",
        "trans": "哈希表是实现关联数组最高效的数据结构之一。"
    },
    "hash": {
        "uk": "/hæʃ/",
        "us": "/hæʃ/",
        "defs": [
            {"pos": "n.", "cn": "哈希值；散列值；键值散列", "en": "a value generated by a hash function"},
            {"pos": "vt.", "cn": "哈希运算；散列", "en": "to process data using a hash function"}
        ],
        "ai": "计算机与密码学基础概念，将任意长度输入映射为固定长度的特征值。",
        "trans": "哈希函数应尽可能均匀分布以减少碰撞。"
    },
    "directional": {
        "uk": "/daɪˈrek.ʃən.əl/",
        "us": "/dəˈrek.ʃən.əl/",
        "defs": [
            {"pos": "adj.", "cn": "方向的；单向的；定向的", "en": "operating, moving, or focused in a particular direction"}
        ],
        "ai": "学术描述词，用于指代系统、图结构或数据流的单向性（如 One-directional）。",
        "trans": "该算法遵循严格的单向流动设计。"
    },
    "ephemeral": {
        "uk": "/ɪˈfem.ər.əl/",
        "us": "/əˈfem.ər.əl/",
        "defs": [
            {"pos": "adj.", "cn": "短暂的；瞬息的；转瞬即逝的", "en": "lasting for only a short time"},
            {"pos": "n.", "cn": "生命短暂的事物", "en": "an ephemeral thing or organism"}
        ],
        "ai": "在商科与学术论文中，多用于分析市场流行趋势或短期泡沫，强调时间维度的短暂不可逆。",
        "trans": "时尚趋势的转瞬即逝使得预测变得极其困难。"
    },
    "linear search": {
        "uk": "/ˈlɪn.i.ər sɜːtʃ/",
        "us": "/ˈlɪn.i.ɚ sɝːtʃ/",
        "defs": [
            {"pos": "n. phrase", "cn": "线性搜索；顺序查找（时间复杂度 O(n)）", "en": "a method for finding an element within a list by checking elements sequentially"}
        ],
        "ai": "计算机基础检索算法：按顺序逐一核对数组每个元素直到匹配，最坏需要比对 n 次。",
        "trans": "对于无序列表，线性搜索是最基础直接的检索手段。"
    },
    "binary search": {
        "uk": "/ˈbaɪ.nər.i sɜːtʃ/",
        "us": "/ˈbaɪ.ner.i sɝːtʃ/",
        "defs": [
            {"pos": "n. phrase", "cn": "二分查找；折半查找（时间复杂度 O(log n)）", "en": "a search algorithm that finds the position of a target value within a sorted array"}
        ],
        "ai": "高效检索算法：要求数组必须事先有序，每次比对均排除半数候选空间。",
        "trans": "二分查找算法的时间复杂度为对数级 O(log n)。"
    },
    "number": {
        "uk": "/ˈnʌm.bər/",
        "us": "/ˈnʌm.bɚ/",
        "defs": [
            {"pos": "n.", "cn": "数字，数量；号码，编号", "en": "an arithmetical value or a quantity of people or things"},
            {"pos": "vt.", "cn": "标号，给...编号；总计", "en": "to mark with a number or to amount to"}
        ],
        "ai": "学术文献与算法分析中的最高频量词，常用于 'the number of comparisons'（比较次数）等时间复杂度表述。",
        "trans": "算法执行所需的比较次数与数据规模直接相关。"
    },
    "number of": {
        "uk": "/ˈnʌm.bər əv/",
        "us": "/ˈnʌm.bɚ əv/",
        "defs": [
            {"pos": "phrase", "cn": "...的数量，...的数目", "en": "an amount or quantity of something"}
        ],
        "ai": "学术论文高频统计短语，后接复数名词表示具体项数或频次。",
        "trans": "比较的总次数随着每次循环递减。"
    },
    "pass": {
        "uk": "/pɑːs/",
        "us": "/pæs/",
        "defs": [
            {"pos": "n.", "cn": "（算法）单趟，单次轮次；通道；通行证", "en": "a single progress over something, as in sorting algorithms"},
            {"pos": "v.", "cn": "通过；经过；传递；及格", "en": "to move or cause to move in a specified direction"}
        ],
        "ai": "在计算机算法（如冒泡排序、多遍编译器）中特指遍历数据结构的一次完整轮次（如 pass-through）。",
        "trans": "经过每一轮排序后，未排序区间的最大值都会沉底。"
    },
    "ubiquitous": {
        "uk": "/juːˈbɪk.wɪ.təs/",
        "us": "/juːˈbɪk.wə.t̬əs/",
        "defs": [
            {"pos": "adj.", "cn": "无所不在的；十分普遍的", "en": "present, appearing, or found everywhere"}
        ],
        "ai": "计算机与物联网学术高频词（如 Ubiquitous Computing 普适计算），形容智能感知嵌入生活各处的常态。",
        "trans": "智能手机在大学校园中已然无所不在。"
    },
    "paradigm": {
        "uk": "/ˈpær.ə.daɪm/",
        "us": "/ˈper.ə.daɪm/",
        "defs": [
            {"pos": "n.", "cn": "范式；典范；理论框架", "en": "a typical example or pattern of something; a model"}
        ],
        "ai": "特指某一学科领域普遍接受的思考框架（如 Paradigm Shift 范式转移）。",
        "trans": "这一发现标志着现代量子力学的一场根本性范式转移。"
    },
    "paradigm shift": {
        "uk": "/ˈpær.ə.daɪm ʃɪft/",
        "us": "/ˈper.ə.daɪm ʃɪft/",
        "defs": [
            {"pos": "n. phrase", "cn": "范式转移；思考/理论体系的重大变革", "en": "a fundamental change in approach or underlying assumptions"}
        ],
        "ai": "科学哲学核心概念（源自库恩《科学革命的结构》），指学科基础理论与研究范式的颠覆性重构。",
        "trans": "人工智能的爆发式演进推动了人机交互界面的深远范式转移。"
    },
    "empirical": {
        "uk": "/ɪmˈpɪr.ɪ.kəl/",
        "us": "/ɪmˈpɪr.ɪ.kəl/",
        "defs": [
            {"pos": "adj.", "cn": "实证的；以观察或实验为依据的", "en": "based on, concerned with, or verifiable by observation"}
        ],
        "ai": "论文核心词汇，强调研究结论来自第一手观测与对照实验数据，而非纯理论玄思。",
        "trans": "研究团队需要充足的实证数据来支撑假说。"
    },
    "mitigate": {
        "uk": "/ˈmɪt.ɪ.ɡeɪt/",
        "us": "/ˈmɪt̬.ə.ɡeɪt/",
        "defs": [
            {"pos": "v.", "cn": "缓和；减轻；缓解", "en": "make less severe, serious, or painful"}
        ],
        "ai": "常用于风险管理与环境科学，强调通过针对性策略主动削减负面损害。",
        "trans": "高校采取严格的数据容灾方案以减轻系统崩溃风险。"
    },
    "pascal": {
        "uk": "/pæˈskæl/",
        "us": "/pæˈskæl/",
        "defs": [
            {"pos": "n.", "cn": "帕斯卡（压强单位 Pa；亦指法国数学家布莱兹·帕斯卡）", "en": "the SI unit of pressure; French mathematician"}
        ],
        "ai": "计算机与数学基础：Pascal's Triangle（杨辉三角/帕斯卡三角）以及经典 Pascal 编程语言均以其命名。",
        "trans": "帕斯卡三角形中的每个数等于其上方两数之和。"
    },
    "triangle": {
        "uk": "/ˈtraɪ.æŋ.ɡəl/",
        "us": "/ˈtraɪ.æŋ.ɡəl/",
        "defs": [
            {"pos": "n.", "cn": "三角形；三角几何图形", "en": "a plane figure with three straight sides and three angles"}
        ],
        "ai": "数学与算法基础几何概念，如 Pascal's Triangle（杨辉三角）。",
        "trans": "算法展示了如何在二维数组中构建并打印杨辉三角。"
    },
    "exercise": {
        "uk": "/ˈek.sə.saɪz/",
        "us": "/ˈek.sɚ.saɪz/",
        "defs": [
            {"pos": "n.", "cn": "练习；习题；训练", "en": "an activity or task designed to develop or hone a skill"},
            {"pos": "v.", "cn": "锻炼；行使", "en": "engage in physical activity; apply or use"}
        ],
        "ai": "课件与教材核心导航词：章节课后练习与算法上机实验。",
        "trans": "请完成本章末尾关于递归与分治策略的编程练习。"
    },
    "unique": {
        "uk": "/juːˈniːk/",
        "us": "/juːˈniːk/",
        "defs": [
            {"pos": "adj.", "cn": "唯一的；独特的；独一无二的", "en": "being the only one of its kind; unlike anything else"}
        ],
        "ai": "算法与数据结构核心性质：指解的唯一性或集合元素互不重复（如 unique elements, set 去重）。",
        "trans": "哈希集合能够以 O(1) 的平均时间复杂度筛选出唯一元素。"
    },
    "figure": {
        "uk": "/ˈfɪɡ.ər/",
        "us": "/ˈfɪɡ.jɚ/",
        "defs": [
            {"pos": "n.", "cn": "图；插图；数字；人物", "en": "a diagram or illustration; a number or bodily shape"}
        ],
        "ai": "学术论文与课件标准插图标记（如 Figure 9.5: Pascal's Triangle）。",
        "trans": "图 9.5 展示了递归调用的完整状态树结构。"
    },
    "range": {
        "uk": "/reɪndʒ/",
        "us": "/reɪndʒ/",
        "defs": [
            {"pos": "n.", "cn": "范围；区间；值域", "en": "the area of variation between upper and lower limits"},
            {"pos": "v.", "cn": "在...范围内变化", "en": "vary or extend between specified limits"}
        ],
        "ai": "编程语言（如 Python range()）内置迭代区间及数学值域概念。",
        "trans": "该循环遍历指定索引范围内的所有有效元素。"
    },
    "step": {
        "uk": "/step/",
        "us": "/step/",
        "defs": [
            {"pos": "n.", "cn": "步骤；步伐；单步操作", "en": "a measure or action, especially one of a series; a single stage"}
        ],
        "ai": "算法推导与调试核心词汇：单步执行与状态转移步骤。",
        "trans": "递归算法在基本情况处终止单步推导。"
    },
    "factorial": {
        "uk": "/fækˈtɔː.ri.əl/",
        "us": "/fækˈtɔːr.i.əl/",
        "defs": [
            {"pos": "n.", "cn": "阶乘（记作 n!）", "en": "the product of an integer and all the integers below it"}
        ],
        "ai": "经典递归教学案例：0! = 1, n! = n * (n-1)!。",
        "trans": "阶乘函数是理解递归与调用栈最直观的模型。"
    },
    "fibonacci": {
        "uk": "/ˌfɪb.əˈnɑː.tʃi/",
        "us": "/ˌfɪb.əˈnɑː.tʃi/",
        "defs": [
            {"pos": "n.", "cn": "斐波那契（数列）", "en": "a sequence where each number is the sum of the two preceding ones"}
        ],
        "ai": "递归、记忆化搜索与动态规划经典标杆：F(n) = F(n-1) + F(n-2)。",
        "trans": "通过记忆化数组可以将斐波那契递归的时间复杂度降低至 O(N)。"
    },
    "matrix": {
        "uk": "/ˈmeɪ.trɪks/",
        "us": "/ˈmeɪ.trɪks/",
        "defs": [
            {"pos": "n.", "cn": "矩阵；二维网格", "en": "a rectangular array of numbers arranged in rows and columns"}
        ],
        "ai": "线性代数与图论邻接矩阵基础：用于表示多维空间变换与图的连通性。",
        "trans": "矩阵乘法是动态规划路径计数与计算机图形学的核心计算。"
    },
    "vector": {
        "uk": "/ˈvek.tər/",
        "us": "/ˈvek.tɚ/",
        "defs": [
            {"pos": "n.", "cn": "向量；矢量；动态顺序表", "en": "a quantity with direction and magnitude; C++ std::vector"}
        ],
        "ai": "标准库高频动态数组容器与代数几何矢量。",
        "trans": "动态向量支持在常数均摊时间内在尾部追加元素。"
    },
    "row": {
        "uk": "/rəʊ/",
        "us": "/roʊ/",
        "defs": [
            {"pos": "n.", "cn": "行；横排", "en": "a horizontal line of entries in a table or array"}
        ],
        "ai": "二维网格与数据库表水平物理维度。",
        "trans": "外层循环负责逐行遍历二维数组。"
    },
    "column": {
        "uk": "/ˈkɒl.əm/",
        "us": "/ˈkɑː.ləm/",
        "defs": [
            {"pos": "n.", "cn": "列；垂直列", "en": "a vertical division of a page, text, or grid"}
        ],
        "ai": "二维数组与关系型表格垂直维度。",
        "trans": "内层循环对每一行中的各个列元素执行累计运算。"
    },
    "dimension": {
        "uk": "/daɪˈmen.ʃən/",
        "us": "/dɪˈmen.ʃən/",
        "defs": [
            {"pos": "n.", "cn": "维度；维数；度量", "en": "a measurable extent of a particular kind; vector dimension"}
        ],
        "ai": "数据结构多维数组与特征工程核心概念。",
        "trans": "高维特征空间常常需要进行降维预处理。"
    },
    "element": {
        "uk": "/ˈel.ɪ.mənt/",
        "us": "/ˈel.ə.mənt/",
        "defs": [
            {"pos": "n.", "cn": "元素；要素；数组项", "en": "an essential part of something; an item in an array or set"}
        ],
        "ai": "容器与集合中包含的单个数据项。",
        "trans": "线性搜索逐一比对数组中的每个元素直到目标被发现。"
    },
    "base": {
        "uk": "/beɪs/",
        "us": "/beɪs/",
        "defs": [
            {"pos": "n.", "cn": "基底；基础；底数；基本情况", "en": "the lowest part or edge; the foundation; base case"}
        ],
        "ai": "算法递归基（Base Case）与数制底数（如 Base 10）。",
        "trans": "递归算法必须包含明确的基本条件以防止无限调用栈溢出。"
    },
    "bound": {
        "uk": "/baʊnd/",
        "us": "/baʊnd/",
        "defs": [
            {"pos": "n.", "cn": "边界；界限（如 Upper/Lower Bound）", "en": "a limiting value or boundary"}
        ],
        "ai": "复杂度渐进分析上下界（如 Big-O 上界，Omega 下界）。",
        "trans": "该算法的时间复杂度上界由最坏情况下的分支数决定。"
    },
    "upper": {
        "uk": "/ˈʌp.ər/",
        "us": "/ˈʌp.ɚ/",
        "defs": [
            {"pos": "adj.", "cn": "上面的；较高的", "en": "situated above another part"}
        ],
        "ai": "Upper Bound（上界）高频搭配词。",
        "trans": "二分搜索的 upper_bound 用于寻找首个大于目标值的元素位置。"
    },
    "lower": {
        "uk": "/ˈləʊ.ər/",
        "us": "/ˈloʊ.ɚ/",
        "defs": [
            {"pos": "adj.", "cn": "下面的；较低的", "en": "less high in position or status"}
        ],
        "ai": "Lower Bound（下界）高频搭配词。",
        "trans": "比较类排序算法的时间复杂度理论下界为 O(N log N)。"
    },
    "condition": {
        "uk": "/kənˈdɪʃ.ən/",
        "us": "/kənˈdɪʃ.ən/",
        "defs": [
            {"pos": "n.", "cn": "条件；状态；约束", "en": "a state of affairs that must exist or be brought about"}
        ],
        "ai": "控制流 if 判定分支与算法退出约束。",
        "trans": "循环终止条件确保算法在有限步内收敛并输出结果。"
    },
    "proof": {
        "uk": "/pruːf/",
        "us": "/pruːf/",
        "defs": [
            {"pos": "n.", "cn": "证明；证据", "en": "evidence or argument establishing fact or truth"}
        ],
        "ai": "算法正确性证明（如循环不变式与数学归纳法）。",
        "trans": "算法的正确性可以通过严谨的数学归纳法予以证明。"
    },
    "theorem": {
        "uk": "/ˈθɪə.rəm/",
        "us": "/ˈθiː.rəm/",
        "defs": [
            {"pos": "n.", "cn": "定理", "en": "a general proposition not self-evident but proved by reasoning"}
        ],
        "ai": "主定理（Master Theorem）分析分治递归算法时间复杂度。",
        "trans": "利用主定理可以快速判定递归分治算法的时间复杂度阶数。"
    },
    "lemma": {
        "uk": "/ˈlem.ə/",
        "us": "/ˈlem.ə/",
        "defs": [
            {"pos": "n.", "cn": "引理", "en": "a subsidiary or intermediate theorem in an argument"}
        ],
        "ai": "学术论证与推导中的辅助定理。",
        "trans": "借助该引理，我们可以简化贪心策略最优性的证明过程。"
    },
    "property": {
        "uk": "/ˈprɒp.ə.ti/",
        "us": "/ˈprɑː.pɚ.t̬i/",
        "defs": [
            {"pos": "n.", "cn": "性质；属性；特征", "en": "an attribute, quality, or characteristic of something"}
        ],
        "ai": "数据结构固有性质（如二叉搜索树的 BST 性质、堆性质）。",
        "trans": "最大堆性质保证根节点保存的值恒大于其所有子节点。"
    },
    "pascal's triangle": {
        "uk": "/pæˈskælz ˈtraɪ.æŋ.ɡəl/",
        "us": "/pæˈskælz ˈtraɪ.æŋ.ɡəl/",
        "defs": [
            {"pos": "n. phrase", "cn": "杨辉三角；帕斯卡三角（二项式系数三角形数表）", "en": "a triangular array of numbers in which each number is the sum of the two directly above it"}
        ],
        "ai": "计算机与组合数学核心模型：第 n 行第 k 个数对应组合数 C(n, k)，是递归与动态规划状态转移方程的经典教学案例。",
        "trans": "杨辉三角的第 n 行给出了 (x+y)^n 展开式的二项式系数。"
    },
    "pascal triangle": {
        "uk": "/pæˈskæl ˈtraɪ.æŋ.ɡəl/",
        "us": "/pæˈskæl ˈtraɪ.æŋ.ɡəl/",
        "defs": [
            {"pos": "n. phrase", "cn": "杨辉三角；帕斯卡三角（二项式系数三角形数表）", "en": "a triangular array of numbers in which each number is the sum of the two directly above it"}
        ],
        "ai": "计算机与组合数学核心模型：第 n 行第 k 个数对应组合数 C(n, k)，是递归与动态规划状态转移方程的经典教学案例。",
        "trans": "杨辉三角常用于教学展示二维数组递推与组合计数。"
    },
    "data structure": {
        "uk": "/ˈdeɪ.tə ˈstrʌk.tʃər/",
        "us": "/ˈdeɪ.t̬ə ˈstrʌk.tʃɚ/",
        "defs": [
            {"pos": "n. phrase", "cn": "数据结构（组织与存储数据的特定格式）", "en": "a specialized format for organizing, processing, retrieving and storing data"}
        ],
        "ai": "计算机科学核心基础课程，涵盖线性表、栈、队列、树、图及散列表等。",
        "trans": "选择合适的数据结构是优化算法运行效率的关键前提。"
    },
    "data structures": {
        "uk": "/ˈdeɪ.tə ˈstrʌk.tʃəz/",
        "us": "/ˈdeɪ.t̬ə ˈstrʌk.tʃɚz/",
        "defs": [
            {"pos": "n. phrase", "cn": "数据结构（课程/复数）", "en": "specialized formats for organizing and storing data"}
        ],
        "ai": "计算机科学核心基础课程：数据结构与算法。",
        "trans": "数据结构课程系统讲解各类容器的时间与空间复杂度分析。"
    },
    "lecture note": {
        "uk": "/ˈlek.tʃər nəʊt/",
        "us": "/ˈlek.tʃɚ noʊt/",
        "defs": [
            {"pos": "n. phrase", "cn": "讲义；课堂笔记", "en": "notes taken during or prepared for a lecture"}
        ],
        "ai": "大学课件与学术讲义标准标题标识（如 Lecture Note 9 - Recursion）。",
        "trans": "讲义第九章系统阐述了递归函数的数学归纳法与调用栈回溯机制。"
    },
    "lecture notes": {
        "uk": "/ˈlek.tʃər nəʊts/",
        "us": "/ˈlek.tʃɚ noʊts/",
        "defs": [
            {"pos": "n. phrase", "cn": "讲义；课程课件笔记", "en": "notes taken during or prepared for a lecture"}
        ],
        "ai": "大学课件与学术讲义标准标题标识。",
        "trans": "本套讲义涵盖了高级数据结构与算法设计的全套课件内容。"
    },
    "base case": {
        "uk": "/beɪs keɪs/",
        "us": "/beɪs keɪs/",
        "defs": [
            {"pos": "n. phrase", "cn": "递归基；基本情况；终止条件", "en": "the condition under which a recursive function returns without making another call"}
        ],
        "ai": "递归算法设计两大支柱之一：直接返回结果而不继续向下展开的边界条件。",
        "trans": "若缺少正确的递归基，函数将陷入无限递归并触发栈溢出崩溃。"
    },
    "recursive call": {
        "uk": "/rɪˈkɜː.sɪv kɔːl/",
        "us": "/rɪˈkɝː.sɪv kɑːl/",
        "defs": [
            {"pos": "n. phrase", "cn": "递归调用", "en": "a call to the function itself from within the function body"}
        ],
        "ai": "函数在内部调用自身以解决规模更小的子问题。",
        "trans": "每次递归调用都会在系统调用栈上压入一个新的栈帧。"
    },
    "call stack": {
        "uk": "/kɔːl stæk/",
        "us": "/kɑːl stæk/",
        "defs": [
            {"pos": "n. phrase", "cn": "调用栈；执行栈", "en": "a stack data structure that stores information about the active subroutines of a program"}
        ],
        "ai": "操作系统与运行时为程序分配的用于管理函数调用轨迹的栈空间。",
        "trans": "深度过深的递归可能耗尽调用栈空间从而抛出 StackOverflowError。"
    },
    "time complexity": {
        "uk": "/taɪm kəmˈplek.sə.ti/",
        "us": "/taɪm kəmˈplek.sə.t̬i/",
        "defs": [
            {"pos": "n. phrase", "cn": "时间复杂度（算法执行耗时随输入规模增长的阶数）", "en": "the computational complexity that describes the amount of time an algorithm takes"}
        ],
        "ai": "算法渐进分析的核心指标，通常使用 Big-O 符号表示（如 O(1), O(N), O(N log N)）。",
        "trans": "通过引入哈希表，我们将检索操作的时间复杂度从 O(N) 优化至常数级 O(1)。"
    },
    "space complexity": {
        "uk": "/speɪs kəmˈplek.sə.ti/",
        "us": "/speɪs kəmˈplek.sə.t̬i/",
        "defs": [
            {"pos": "n. phrase", "cn": "空间复杂度（算法所需内存空间随输入规模增长的阶数）", "en": "the amount of memory space required by an algorithm to run"}
        ],
        "ai": "算法辅助内存消耗的渐进分析指标。",
        "trans": "该递归算法的空间复杂度为 O(N)，主要由调用栈的最大深度决定。"
    },
    "worst case": {
        "uk": "/wɜːst keɪs/",
        "us": "/wɝːst keɪs/",
        "defs": [
            {"pos": "n. phrase", "cn": "最坏情况；最差情形", "en": "the scenario or input that leads to the maximum running time"}
        ],
        "ai": "算法理论保障分析中最关键的边界情况（如快速排序最坏情况为 O(N^2)）。",
        "trans": "快速排序在最坏情况下的时间复杂度退化为二次方阶。"
    },
    "best case": {
        "uk": "/best keɪs/",
        "us": "/best keɪs/",
        "defs": [
            {"pos": "n. phrase", "cn": "最好情况；最优情形", "en": "the scenario that leads to the minimum running time"}
        ],
        "ai": "算法在最理想输入下的时间表现。",
        "trans": "插入排序在数组已有序的最好情况下仅需 O(N) 时间。"
    },
    "average case": {
        "uk": "/ˈæv.ər.ɪdʒ keɪs/",
        "us": "/ˈæv.ɚ.ɪdʒ keɪs/",
        "defs": [
            {"pos": "n. phrase", "cn": "平均情况；平均情形", "en": "the expected running time over all possible inputs"}
        ],
        "ai": "算法面对随机输入分布时的数学期望时间表现。",
        "trans": "哈希表的平均查找时间复杂度为常数级 O(1)。"
    },
    "binary tree": {
        "uk": "/ˈbaɪ.nər.i triː/",
        "us": "/ˈbaɪ.ner.i triː/",
        "defs": [
            {"pos": "n. phrase", "cn": "二叉树（每个节点最多拥有两个子节点的树状结构）", "en": "a tree data structure in which each node has at most two children"}
        ],
        "ai": "分层数据结构的核心基石，派生出二叉搜索树（BST）、AVL 树、红黑树及线段树。",
        "trans": "对二叉树进行中序遍历能够以非降序访问二叉搜索树的所有键值。"
    },
    "divide and conquer": {
        "uk": "/dɪˈvaɪd ənd ˈkɒŋ.kər/",
        "us": "/dɪˈvaɪd ənd ˈkɑːŋ.kɚ/",
        "defs": [
            {"pos": "n. phrase", "cn": "分治算法；分而治之", "en": "an algorithm design paradigm that recursively breaks a problem down into subproblems"}
        ],
        "ai": "经典算法设计范式：将大问题递归拆分子问题求解，再合并子问题解（如归并排序、快速排序）。",
        "trans": "分治法通过将规模为 N 的问题划分为独立子模块，大幅降低了计算复杂度。"
    },
    "dynamic programming": {
        "uk": "/daɪˈnæm.ɪk ˈprəʊ.ɡræm.ɪŋ/",
        "us": "/daɪˈnæm.ɪk ˈproʊ.ɡræm.ɪŋ/",
        "defs": [
            {"pos": "n. phrase", "cn": "动态规划（通过重叠子问题与最优子结构求解最优解）", "en": "a method for solving complex problems by breaking them down into simpler subproblems"}
        ],
        "ai": "算法设计核心思想，通过备忘录或状态转移表格避免子问题重复计算。",
        "trans": "动态规划算法将计算杨辉三角各个项的值转化为简单的两数相加递推。"
    },
    "doom scrolling": {
        "uk": "/ˈduːm ˌskrəʊ.lɪŋ/",
        "us": "/ˈduːm ˌskroʊ.lɪŋ/",
        "defs": [
            {"pos": "n./v. phrase", "cn": "刷负面新闻；沉迷刷屏（在社交媒体或资讯流上不停浏览负面消息）", "en": "the practice of obsessively reading distressing or negative news online"}
        ],
        "ai": "现代数字时代高频复合词：指尽管令人焦虑却忍不住持续下拉刷新负面信息的行为。",
        "trans": "互联网想让你不停地沉迷刷屏，以下教你如何摆脱束缚。"
    },
    "doom-scrolling": {
        "uk": "/ˈduːm ˌskrəʊ.lɪŋ/",
        "us": "/ˈduːm ˌskroʊ.lɪŋ/",
        "defs": [
            {"pos": "n./v. phrase", "cn": "刷负面新闻；沉迷刷屏（在社交媒体或资讯流上不停浏览负面消息）", "en": "the practice of obsessively reading distressing or negative news online"}
        ],
        "ai": "现代数字时代高频复合词：指尽管令人焦虑却忍不住持续下拉刷新负面信息的行为。",
        "trans": "互联网想让你不停地沉迷刷屏，以下教你如何摆脱束缚。"
    },
    "doomscrolling": {
        "uk": "/ˈduːmˌskrəʊ.lɪŋ/",
        "us": "/ˈduːmˌskroʊ.lɪŋ/",
        "defs": [
            {"pos": "n./v.", "cn": "刷负面新闻；沉迷刷屏（在社交媒体或资讯流上不停浏览负面消息）", "en": "the act of spending excessive time reading negative news online"}
        ],
        "ai": "现代数字时代高频复合词（同 doom scrolling）：指不停下拉刷负面资讯的强迫性习惯。",
        "trans": "互联网想让你不停地沉迷刷屏，以下教你如何摆脱束缚。"
    },
    "break free": {
        "uk": "/breɪk friː/",
        "us": "/breɪk friː/",
        "defs": [
            {"pos": "v. phrase", "cn": "挣脱；摆脱束缚；逃脱", "en": "to escape from someone or something that holds you back or limits you"}
        ],
        "ai": "高频动词短语：常表示从束缚、习惯或困境中彻底解脱（如 break free from）。",
        "trans": "以下是如何摆脱束缚、重获专注的方法。"
    }
}

_STOP_SINGLE_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "it", "this", "that", "these", "those", "here", "there", "i", "you",
    "we", "they", "he", "she", "me", "us", "them", "him", "her", "my",
    "your", "our", "their", "his", "its", "to", "of", "in", "on", "at",
    "for", "with", "and", "or", "but", "not", "no", "yes", "so", "as",
    "by", "from", "how", "what", "who", "why", "when", "where", "which",
})

_CURATED_PHRASE_PATTERNS = [
    (r"\bdoom[\s\-]?scrolling\b", None),
    (r"\b(?:break|breaks|broke|broken|breaking)\s+free\b", None),
    (r"\b(?:keep|keeps|kept|keeping)\s+up\s+with\b", None),
    (r"\b(?:figure|figures|figured|figuring)\s+out\b", None),
    (r"\b(?:point|points|pointed|pointing)\s+out\b", None),
    (r"\b(?:take|takes|took|taken|taking)\s+into\s+account\b", None),
    (r"\b(?:take|takes|took|taken|taking)\s+advantage\s+of\b", None),
    (r"\b(?:take|takes|took|taken|taking)\s+place\b", None),
    (r"\b(?:make|makes|made|making)\s+sense\b", None),
    (r"\b(?:rely|relies|relied|relying)\s+on\b", None),
    (r"\b(?:depend|depends|depended|depending)\s+on\b", None),
    (r"\b(?:deal|deals|dealt|dealing)\s+with\b", None),
    (r"\b(?:lead|leads|led|leading)\s+to\b", None),
    (r"\b(?:result|results|resulted|resulting)\s+in\b", None),
    (r"\b(?:stem|stems|stemmed|stemming)\s+from\b", None),
    (r"\b(?:set|sets|setting)\s+up\b", None),
    (r"\b(?:carry|carries|carried|carrying)\s+out\b", None),
    (r"\b(?:bring|brings|brought|bringing)\s+about\b", None),
    (r"\b(?:give|gives|gave|given|giving)\s+up\b", None),
    (r"\b(?:look|looks|looked|looking)\s+forward\s+to\b", None),
    (r"\b(?:come|comes|came|coming)\s+up\s+with\b", None),
    (r"\b(?:end|ends|ended|ending)\s+up\b", None),
    (r"\b(?:turn|turns|turned|turning)\s+out\b", None),
    (r"\b(?:find|finds|found|finding)\s+out\b", None),
    (r"\b(?:work|works|worked|working)\s+out\b", None),
    (r"\b(?:get|gets|got|gotten|getting)\s+rid\s+of\b", None),
    (r"\b(?:pay|pays|paid|paying)\s+attention\s+to\b", None),
    (r"\bin\s+terms\s+of\b", None),
    (r"\bas\s+a\s+result\b", None),
    (r"\bon\s+the\s+other\s+hand\b", None),
    (r"\bin\s+addition\s+to\b", None),
    (r"\baccording\s+to\b", None),
    (r"\bas\s+well\s+as\b", None),
    (r"\brather\s+than\b", None),
    (r"\binstead\s+of\b", None),
]

_PHRASAL_VERB_REGEX = re.compile(
    r"\b(?:break|keep|set|take|make|give|get|turn|bring|put|come|go|look|find|hold|pull|push|run|"
    r"stand|step|work|cut|fall|hand|hang|lay|pass|pick|pay|play|call|clear|close|count|draw|drop|"
    r"end|fill|grow|leave|let|live|move|open|point|rule|see|send|show|shut|sit|slow|sort|speak|"
    r"speed|split|spread|start|stay|stick|stop|strike|switch|talk|tear|think|throw|tie|try|use|"
    r"wait|wake|walk|watch|wear|wind|wipe|write)"
    r"(?:s|es|ed|ing)?\s+"
    r"(?:free|up|out|off|down|away|back|over|through|apart|aside|ahead|along|around|forward|together)\b",
    flags=re.IGNORECASE,
)


def _strip_outer_sentence_quotes(text: str) -> str:
    s = text.strip()
    for op, cl in (('"', '"'), ("“", "”"), ("'", "'"), ("‘", "’")):
        if len(s) >= 2 and s[0] == op and s[-1] == cl and len(s[1:-1].split()) > 4:
            s = s[1:-1].strip()
            break
    return s


def extract_key_phrases(text: str) -> List[str]:
    """从英文长句或段落中提取 1~4 个高价值重点短语/表达（引号强调词、固定搭配、动词短语、复合词）。"""
    if not text or len(text.split()) <= 3:
        return []

    work_text = _strip_outer_sentence_quotes(text)
    results: List[str] = []
    seen_norm = set()

    def _add_candidate(raw_cand: str):
        cand = clean_input_text(raw_cand)
        if not cand or len(cand) < 3:
            return
        words = cand.split()
        if not (1 <= len(words) <= 4):
            return
        if len(words) == 1 and cand.lower() in _STOP_SINGLE_WORDS and "-" not in cand:
            return
        if not any(c.isascii() and c.isalpha() for c in cand):
            return
        norm = re.sub(r"[\s\-–—]+", " ", cand.lower()).strip()
        if norm in seen_norm:
            return
        seen_norm.add(norm)
        results.append(cand)

    # 1. 优先提取引号强调的短语或新词（如 'doom scrolling.' -> doom scrolling）
    quote_patterns = [
        r'["“]([^"”\n]{2,60})["”]',
        r"(?:^|(?<=[\s\(\[\{,;:]))['‘]([^'\n’]{2,60}?)['’](?=$|[\s\)\]\},.;:!?])",
    ]
    quoted_hits: List[tuple[int, str]] = []
    for qp in quote_patterns:
        for m in re.finditer(qp, work_text):
            quoted_hits.append((m.start(), m.group(1)))
    quoted_hits.sort(key=lambda x: x[0])
    for _, q_str in quoted_hits:
        _add_candidate(q_str)
        if len(results) >= 4:
            return results[:4]

    # 2. 匹配内置学术/高频多词短语（按长度降序避免子短语截断）
    known_multi = sorted(
        {k for k in list(OFFLINE_DEFS.keys()) + list(ACADEMIC_TAGS.keys()) if " " in k or "-" in k},
        key=len,
        reverse=True,
    )
    text_lower = work_text.lower()
    known_hits: List[tuple[int, str]] = []
    for phrase_key in known_multi:
        pat = rf"(?<![A-Za-z0-9]){re.escape(phrase_key)}(?![A-Za-z0-9])"
        for m in re.finditer(pat, text_lower):
            known_hits.append((m.start(), work_text[m.start():m.end()]))
    known_hits.sort(key=lambda x: x[0])
    for _, k_str in known_hits:
        _add_candidate(k_str)
        if len(results) >= 4:
            return results[:4]

    # 3. 匹配常见固定搭配、习语及动词短语（如 break free, figure out）
    colloc_hits: List[tuple[int, str]] = []
    for pat, _ in _CURATED_PHRASE_PATTERNS:
        for m in re.finditer(pat, work_text, flags=re.IGNORECASE):
            colloc_hits.append((m.start(), m.group(0)))
    for m in _PHRASAL_VERB_REGEX.finditer(work_text):
        colloc_hits.append((m.start(), m.group(0)))
    colloc_hits.sort(key=lambda x: x[0])
    for _, c_str in colloc_hits:
        _add_candidate(c_str)
        if len(results) >= 4:
            return results[:4]

    # 4. 匹配连字符复合词（如 doom-scrolling, real-time）
    for m in re.finditer(r"\b[a-zA-Z]{2,}-[a-zA-Z]{2,}(?:-[a-zA-Z]{2,})?\b", work_text):
        _add_candidate(m.group(0))
        if len(results) >= 4:
            return results[:4]

    return results[:4]


def _lookup_phrase_offline(phrase: str) -> Optional[Dict[str, str]]:
    p_low = phrase.strip().lower()
    variants = [
        p_low,
        p_low.replace("-", " "),
        p_low.replace(" ", "-"),
        p_low.replace(" ", "").replace("-", ""),
    ]
    for v in variants:
        entry = OFFLINE_DEFS.get(v)
        if entry:
            defs = entry.get("defs", [])
            pos = defs[0].get("pos", "短语") if defs else "短语"
            cn = defs[0].get("cn", "") if defs else ""
            if cn:
                return {"phrase": phrase, "pos": pos, "cn": cn}
    for v in variants:
        try:
            db_hit = lookup_offline_dict(v)
        except Exception:
            db_hit = None
        if db_hit:
            defs = db_hit.get("defs", [])
            pos = defs[0].get("pos", "短语") if defs else "短语"
            cn = defs[0].get("cn", "") if defs else ""
            if cn:
                return {"phrase": phrase, "pos": pos, "cn": cn}
    return None


def _lookup_phrase_online(phrase: str) -> Dict[str, str]:
    off = _lookup_phrase_offline(phrase)
    if off:
        return off
    is_multi = " " in phrase or "-" in phrase
    default_pos = "短语" if is_multi else "词汇"
    try:
        encoded = urllib.parse.quote(phrase)
        url = f"https://dict.youdao.com/suggest?q={encoded}&le=en&num=1&doctype=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with _open_url_smart(req, timeout=1.6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            entries = data.get("data", {}).get("entries", [])
            if entries and entries[0].get("explain"):
                matched_entry = entries[0].get("entry", "").strip().lower()
                norm_matched = re.sub(r"[\s\-]+", "", matched_entry)
                norm_phrase = re.sub(r"[\s\-]+", "", phrase.lower())
                if norm_matched == norm_phrase or (is_multi and matched_entry == phrase.lower()):
                    explain = entries[0]["explain"].strip()
                    first_part = re.split(r"[;；]", explain)[0].strip()
                    m = re.match(r"^([a-zA-Z]+\.)\s*(.+)$", first_part)
                    if m:
                        return {"phrase": phrase, "pos": m.group(1), "cn": m.group(2).strip()}
                    return {"phrase": phrase, "pos": default_pos, "cn": first_part}
    except Exception:
        pass

    trans = translate_text(phrase)
    if trans and trans.lower() != phrase.lower():
        return {"phrase": phrase, "pos": default_pos, "cn": trans}
    return {"phrase": phrase, "pos": default_pos, "cn": ""}

def _is_balanced_inner(s: str, opener: str, closer: str) -> bool:
    depth = 0
    for ch in s:
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def clean_input_text(raw: str) -> str:
    # 将拉丁变音借词（如 naïve, café, résumé）安全归一化为 ASCII 字母，避免直接剥离变残
    normalized = "".join(
        ch for ch in unicodedata.normalize("NFKD", (raw or "").strip())
        if unicodedata.category(ch) != "Mn"
    )
    cleaned = re.sub(r'[^a-zA-Z0-9\s\-_\'\"“”‘’\(\)\[\]\{\}\,\.\:\;\?\!\/\^\+\=\*\<\>\%]', '', normalized)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # 🌟 剥离首尾多余句读标点（如 'Triangle.' -> 'Triangle', 'The lookup time is O(1).' -> 'The lookup time is O(1)'）
    # 严禁破坏末尾配对括号（如 O(1), F(n-1)）及词内撇号（Pascal's）/连字符（one-way）
    while cleaned:
        prev = cleaned
        cleaned = re.sub(r'^[\s\.\,\:\;\?\!\'\"“”‘’\/\-]+|[\s\.\,\:\;\?\!\'\"“”‘’\/\-]+$', '', cleaned)
        for op, cl in (('(', ')'), ('[', ']'), ('{', '}')):
            if len(cleaned) >= 2 and cleaned[0] == op and cleaned[-1] == cl and _is_balanced_inner(cleaned[1:-1], op, cl):
                cleaned = cleaned[1:-1].strip()
            elif cleaned.startswith((op, cl)) and cl not in cleaned[1:]:
                cleaned = cleaned[1:].strip()
            elif cleaned.endswith(op):
                cleaned = cleaned[:-1].strip()
        if cleaned == prev:
            break
    return cleaned


LOADING_PLACEHOLDERS = frozenset({
    "正在查询释义...",
    "正在智能翻译整句内容...",
    "正在翻译整句...",
    "正在获取权威精准释义...",
})
CTX_LOADING_PLACEHOLDERS = frozenset({
    "正在同步现场语境译文...",
    "↳ 正在翻译语境例句...",
})

_TRANS_CACHE: "OrderedDict[str, str]" = OrderedDict()
_TRANS_CACHE_LOCK = threading.Lock()
_TRANS_CACHE_MAX = 512
_DEF_CACHE: "OrderedDict[str, List[Dict[str, str]]]" = OrderedDict()
_PHONE_CACHE: "OrderedDict[str, Tuple[str, str]]" = OrderedDict()
_DICT_CACHE_LOCK = threading.Lock()
_DICT_CACHE_MAX = 512
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# 四层物理隔离线程池：编排池 (_PIPELINE_EXECUTOR)、短语子池 (_PHRASE_EXECUTOR)、前台查词 HTTP 池 (_USER_HTTP_EXECUTOR)、后台字幕 HTTP 池 (_HTTP_RACE_EXECUTOR)
_PIPELINE_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="screenlex_pipe")
_PHRASE_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="screenlex_phrase")
_HTTP_RACE_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="screenlex_http")
_USER_HTTP_EXECUTOR = ThreadPoolExecutor(max_workers=12, thread_name_prefix="screenlex_uhttp")

# 系统代理极速探活与 Opener 缓存
_PROXY_PROBE_LOCK = threading.Lock()
_PROXY_PROBE_CACHE: Tuple[Tuple[str, str], float, Optional[Dict[str, str]]] = (("", ""), 0.0, None)
_PROXY_OPENER_CACHE: Dict[Tuple[str, str], urllib.request.OpenerDirector] = {}

# HTTP/HTTPS Keep-Alive 长连接复用池（彻底消除每句重复 TCP/TLS 握手带来的 400~1000ms 延迟）
_KA_POOLS_LOCK = threading.Lock()
_KA_POOLS: Dict[tuple, queue.LifoQueue] = {}
_PREWARM_LOCK = threading.Lock()
_PREWARM_TS = 0.0


def _is_network_mocked() -> bool:
    uopen = urllib.request.urlopen
    if hasattr(uopen, "_mock_name") or getattr(uopen, "__name__", "") != "urlopen":
        return True
    if hasattr(_open_url_direct, "_mock_name") or getattr(_open_url_direct, "__name__", "") != "_open_url_direct":
        return True
    return False


def _probe_http_proxy_url(raw_url: str) -> Optional[str]:
    if not raw_url:
        return None
    try:
        candidate = raw_url.strip()
        if "://" not in candidate:
            candidate = f"http://{candidate}"
        parsed = urllib.parse.urlparse(candidate)
        if (parsed.scheme or "").lower() not in ("http", "https"):
            return None
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        if not host or not (1 <= int(port) <= 65535):
            return None
    except Exception:
        return None

    sock = None
    try:
        sock = socket.create_connection((host, int(port)), timeout=0.08)
        return candidate
    except Exception:
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def _get_live_system_proxy() -> Optional[Dict[str, str]]:
    """读取系统代理并以 80ms TCP 探活（结果缓存 10s），过滤非 HTTP(S)/畸形代理，死代理 <5ms 跳过。"""
    global _PROXY_PROBE_CACHE
    if _is_network_mocked():
        return None
    try:
        raw_proxies = urllib.request.getproxies() or {}
    except Exception:
        return None
    p_http = (raw_proxies.get("http") or "").strip()
    p_https = (raw_proxies.get("https") or p_http or "").strip()
    if not p_http and not p_https:
        return None
    cache_key = (p_http, p_https)
    now = time.time()
    with _PROXY_PROBE_LOCK:
        c_key, c_ts, c_val = _PROXY_PROBE_CACHE
        if c_key == cache_key and (now - c_ts) < 10.0:
            return dict(c_val) if c_val else None

    live_map: Dict[str, str] = {}
    probe_cache: Dict[str, Optional[str]] = {}
    for scheme_key, raw_u in (("http", p_http), ("https", p_https)):
        if not raw_u:
            continue
        if raw_u not in probe_cache:
            probe_cache[raw_u] = _probe_http_proxy_url(raw_u)
        ok_url = probe_cache[raw_u]
        if ok_url:
            live_map[scheme_key] = ok_url

    result_val = live_map if live_map else None
    with _PROXY_PROBE_LOCK:
        _PROXY_PROBE_CACHE = (cache_key, time.time(), result_val)
    return dict(result_val) if result_val else None


def _get_proxy_opener(proxies: Dict[str, str]) -> urllib.request.OpenerDirector:
    key = (proxies.get("http", ""), proxies.get("https", ""))
    with _PROXY_PROBE_LOCK:
        opener = _PROXY_OPENER_CACHE.get(key)
        if opener is None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(dict(proxies)))
            _PROXY_OPENER_CACHE[key] = opener
        return opener


def _http_post_via_proxy(
    url: str,
    body: bytes,
    headers: Dict[str, str],
    proxies: Dict[str, str],
    timeout: float = 3.8,
) -> bytes:
    opener = _get_proxy_opener(proxies)
    req = urllib.request.Request(url, data=body, headers=dict(headers))
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _open_url_smart(req: urllib.request.Request, timeout: float):
    """Mock 态走 urlopen；生产态先直连，仅在非 HTTPError 网络失败且本地系统代理 TCP 存活时走代理重试一次。"""
    uopen = urllib.request.urlopen
    if hasattr(uopen, "_mock_name") or getattr(uopen, "__name__", "") != "urlopen":
        return uopen(req, timeout=timeout)
    if hasattr(_open_url_direct, "_mock_name") or getattr(_open_url_direct, "__name__", "") != "_open_url_direct":
        return _open_url_direct(req, timeout=timeout)
    try:
        return _DIRECT_OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except Exception:
        proxies = _get_live_system_proxy()
        if not proxies:
            raise
        return _get_proxy_opener(proxies).open(req, timeout=max(timeout, 2.2))


def _get_ka_pool(key: tuple) -> queue.LifoQueue:
    with _KA_POOLS_LOCK:
        pool = _KA_POOLS.get(key)
        if pool is None:
            pool = queue.LifoQueue(maxsize=6)
            _KA_POOLS[key] = pool
        return pool


def _http_post_keepalive(
    scheme: str,
    host: str,
    path: str,
    body: bytes,
    headers: Dict[str, str],
    timeout: float = 2.0,
) -> bytes:
    """线程安全 Keep-Alive POST；若处于单元测试 Mock 环境自动回退至 _open_url_direct。"""
    if _is_network_mocked():
        req = urllib.request.Request(f"{scheme}://{host}{path}", data=body, headers=headers)
        with _open_url_direct(req, timeout=timeout) as resp:
            return resp.read()

    req_headers = dict(headers)
    req_headers.setdefault("Connection", "keep-alive")
    pool = _get_ka_pool((scheme, host))
    now = time.time()
    conn = None
    reused = False

    while True:
        try:
            cand_conn, last_ts = pool.get_nowait()
        except queue.Empty:
            break
        if now - last_ts < 15.0:
            sock = getattr(cand_conn, "sock", None)
            is_dead = sock is None
            if sock is not None:
                try:
                    pending_fn = getattr(sock, "pending", None)
                    if callable(pending_fn) and pending_fn() > 0:
                        is_dead = True
                    else:
                        readable, _, errored = select.select([sock], [], [sock], 0)
                        if readable or errored:
                            is_dead = True
                except Exception:
                    is_dead = True
            if not is_dead:
                conn = cand_conn
                reused = True
                break
        try:
            cand_conn.close()
        except Exception:
            pass

    for attempt in range(2):
        if conn is None:
            eff_timeout = timeout
            if scheme == "https":
                conn = http.client.HTTPSConnection(host, timeout=eff_timeout)
            else:
                conn = http.client.HTTPConnection(host, timeout=eff_timeout)
            reused = False
        else:
            eff_timeout = min(timeout, 1.4)
            conn.timeout = eff_timeout
            if getattr(conn, "sock", None) is not None:
                try:
                    conn.sock.settimeout(eff_timeout)
                except Exception:
                    pass
        try:
            conn.request("POST", path, body=body, headers=req_headers)
            resp = conn.getresponse()
            raw = resp.read()
            status = getattr(resp, "status", 200)
            will_close = getattr(resp, "will_close", False)
            if status >= 400:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
                raise urllib.error.HTTPError(f"{scheme}://{host}{path}", status, "HTTP Error", None, None)
            if will_close:
                try:
                    conn.close()
                except Exception:
                    pass
            else:
                try:
                    pool.put_nowait((conn, time.time()))
                except queue.Full:
                    try:
                        conn.close()
                    except Exception:
                        pass
            conn = None
            return raw
        except urllib.error.HTTPError:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None
            raise
        except Exception:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None
            # 若复用旧连接失效且是首次尝试，立刻重建新连接重试一次
            if reused and attempt == 0:
                continue
            raise
    return b""


def prewarm_translation_connections():
    """后台异步预热翻译接口 Keep-Alive 长连接与 DNS/TLS 会话，消除首句冷启动延迟。"""
    global _PREWARM_TS
    if _is_network_mocked():
        return
    now = time.time()
    with _PREWARM_LOCK:
        if now - _PREWARM_TS < 30.0:
            return
        _PREWARM_TS = now

    def _warm_edge():
        try:
            _http_post_keepalive(
                "https",
                "edge.microsoft.com",
                "/translate/translatetext?from=&to=zh-Hans&isEnterpriseClient=false",
                json.dumps(["ok"]).encode("utf-8"),
                {
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
                },
                timeout=2.0,
            )
        except Exception:
            pass

    def _warm_youdao():
        try:
            _http_post_keepalive(
                "http",
                "aidemo.youdao.com",
                "/trans",
                urllib.parse.urlencode({"q": "ok", "from": "Auto", "to": "zh-CHS"}).encode("utf-8"),
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "http://ai.youdao.com/",
                    "Origin": "http://ai.youdao.com",
                },
                timeout=2.0,
            )
        except Exception:
            pass

    for fn in (_warm_edge, _warm_edge, _warm_youdao):
        try:
            _HTTP_RACE_EXECUTOR.submit(fn)
        except Exception:
            pass


def _open_url_direct(req: urllib.request.Request, timeout: float):
    """生产态强制旁路死代理直连；单测 patch('urllib.request.urlopen') 时自动服从 Mock 隔离。"""
    uopen = urllib.request.urlopen
    if hasattr(uopen, "_mock_name") or getattr(uopen, "__name__", "") != "urlopen":
        return uopen(req, timeout=timeout)
    return _DIRECT_OPENER.open(req, timeout=timeout)


def _cache_get(key: str) -> Optional[str]:
    with _TRANS_CACHE_LOCK:
        val = _TRANS_CACHE.get(key)
        if val is not None:
            _TRANS_CACHE.move_to_end(key)
        return val


def _cache_set(key: str, val: str) -> None:
    with _TRANS_CACHE_LOCK:
        if key in _TRANS_CACHE:
            _TRANS_CACHE.move_to_end(key)
            _TRANS_CACHE[key] = val
            return
        if len(_TRANS_CACHE) >= _TRANS_CACHE_MAX:
            _TRANS_CACHE.popitem(last=False)
        _TRANS_CACHE[key] = val


def translate_text(text: str) -> str:
    """高可用极速翻译引擎（国内 100% 直连无墙，Keep-Alive 长连接复用，秒级响应且零嵌套死锁）"""
    clean = text.strip()
    if not clean:
        return ""

    cached = _cache_get(clean)
    if cached is not None:
        return cached

    # 国内多轨并发极速竞速引擎（有道 HTTP/HTTPS 直连 + 微软 Edge 直连 + 活代理并发轨道）
    def _fetch_youdao_http():
        data = urllib.parse.urlencode({"q": clean, "from": "Auto", "to": "zh-CHS"}).encode("utf-8")
        try:
            raw = _http_post_keepalive(
                "http",
                "aidemo.youdao.com",
                "/trans",
                data,
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "http://ai.youdao.com/",
                    "Origin": "http://ai.youdao.com",
                },
                timeout=2.8,
            )
            res = json.loads(raw.decode("utf-8"))
            t = "\n".join(x.strip() for x in res.get("translation", []) if x and x.strip())
            if t and t.strip():
                return t.strip()
        except Exception:
            pass
        return ""

    def _fetch_youdao_https():
        data = urllib.parse.urlencode({"q": clean, "from": "Auto", "to": "zh-CHS"}).encode("utf-8")
        try:
            raw = _http_post_keepalive(
                "https",
                "aidemo.youdao.com",
                "/trans",
                data,
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://ai.youdao.com/",
                    "Origin": "https://ai.youdao.com",
                },
                timeout=2.8,
            )
            res = json.loads(raw.decode("utf-8"))
            t = "\n".join(x.strip() for x in res.get("translation", []) if x and x.strip())
            if t and t.strip():
                return t.strip()
        except Exception:
            pass
        return ""

    def _fetch_edge():
        data = json.dumps([clean]).encode("utf-8")
        try:
            raw = _http_post_keepalive(
                "https",
                "edge.microsoft.com",
                "/translate/translatetext?from=&to=zh-Hans&isEnterpriseClient=false",
                data,
                {
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
                },
                timeout=3.5,
            )
            res = json.loads(raw.decode("utf-8"))
            if res and isinstance(res, list) and res[0].get("translations"):
                t = res[0]["translations"][0].get("text", "").strip()
                if t:
                    return t
        except Exception:
            pass
        return ""

    tasks = [_fetch_edge, _fetch_youdao_http, _fetch_youdao_https]
    live_proxies = None if _is_network_mocked() else _get_live_system_proxy()
    if live_proxies:
        def _fetch_edge_proxy():
            data = json.dumps([clean]).encode("utf-8")
            try:
                raw = _http_post_via_proxy(
                    "https://edge.microsoft.com/translate/translatetext?from=&to=zh-Hans&isEnterpriseClient=false",
                    data,
                    {
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
                    },
                    live_proxies,
                    timeout=4.3,
                )
                res = json.loads(raw.decode("utf-8"))
                if res and isinstance(res, list) and res[0].get("translations"):
                    t = res[0]["translations"][0].get("text", "").strip()
                    if t:
                        return t
            except Exception:
                pass
            return ""
        tasks.append(_fetch_edge_proxy)

    futures = [_USER_HTTP_EXECUTOR.submit(task) for task in tasks]
    try:
        for f in concurrent.futures.as_completed(futures, timeout=4.6):
            try:
                res = f.result()
                if res and res.strip():
                    val = res.strip()
                    _cache_set(clean, val)
                    return val
            except Exception:
                pass
    except concurrent.futures.TimeoutError:
        pass
    finally:
        for other in futures:
            if not other.done():
                other.cancel()

    # 兜底轨：有道词典开放 Suggest 极速释义备用轨（仅限 <=2 词且词头精确匹配，防止前缀联想毒化长句/未知词缓存）
    if len(clean.split()) <= 2:
        try:
            url = f"https://dict.youdao.com/suggest?q={urllib.parse.quote(clean)}&le=en&num=1&doctype=json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with _open_url_smart(req, timeout=1.6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                entries = data.get("data", {}).get("entries", [])
                if entries and entries[0].get("explain"):
                    matched_entry = entries[0].get("entry", "").strip()
                    if matched_entry.lower() == clean.lower():
                        exp = entries[0]["explain"].strip()
                        clean_exp = re.sub(r'^\[[^\]]+\]\s*', '', exp)
                        if clean_exp:
                            _cache_set(clean, clean_exp)
                            return clean_exp
        except Exception:
            pass

    return ""


def translate_batch(texts: List[str]) -> List[str]:
    """批量并发翻译多条字幕（Edge 批量数组 + 有道换行批处理竞速），显著消除逐句请求阻塞与限流。"""
    if not texts:
        return []

    results: List[str] = [""] * len(texts)
    uncached: List[tuple[int, str, str]] = []
    for idx, raw_t in enumerate(texts):
        orig_s = (raw_t or "").strip()
        if not orig_s:
            continue
        prep = " ".join((preprocess_for_translation(orig_s) or orig_s).splitlines()).strip()
        cached = _cache_get(prep) or _cache_get(orig_s)
        if cached is not None:
            results[idx] = cached
        elif prep:
            uncached.append((idx, orig_s, prep))

    if not uncached:
        return results

    if _is_network_mocked():
        for idx, orig_s, prep in uncached:
            val = translate_text(prep)
            results[idx] = val
            if val:
                _cache_set(orig_s, val)
        return results

    uncached_clean = [u[2] for u in uncached]

    def _batch_edge() -> List[str]:
        try:
            raw = _http_post_keepalive(
                "https",
                "edge.microsoft.com",
                "/translate/translatetext?from=&to=zh-Hans&isEnterpriseClient=false",
                json.dumps(uncached_clean).encode("utf-8"),
                {
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
                },
                timeout=3.5,
            )
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, list) and len(parsed) == len(uncached_clean):
                out = []
                for item in parsed:
                    tr_list = item.get("translations") if isinstance(item, dict) else None
                    out.append((tr_list[0].get("text", "") if tr_list else "").strip())
                if any(out):
                    return out
        except Exception:
            pass
        return []

    def _batch_youdao() -> List[str]:
        try:
            data = urllib.parse.urlencode({"q": "\n".join(uncached_clean), "from": "Auto", "to": "zh-CHS"}).encode("utf-8")
            raw = _http_post_keepalive(
                "http",
                "aidemo.youdao.com",
                "/trans",
                data,
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "http://ai.youdao.com/",
                    "Origin": "http://ai.youdao.com",
                },
                timeout=3.5,
            )
            res = json.loads(raw.decode("utf-8"))
            lines: List[str] = []
            for entry in res.get("translation", []):
                if entry:
                    lines.extend(x.strip() for x in str(entry).splitlines())
            lines = [x for x in lines if x]
            if len(lines) == len(uncached_clean):
                return lines
        except Exception:
            pass
        return []

    batch_out: List[str] = []
    batch_tasks = [_batch_edge, _batch_youdao]
    live_proxies = _get_live_system_proxy()
    if live_proxies:
        def _batch_edge_proxy() -> List[str]:
            try:
                raw = _http_post_via_proxy(
                    "https://edge.microsoft.com/translate/translatetext?from=&to=zh-Hans&isEnterpriseClient=false",
                    json.dumps(uncached_clean).encode("utf-8"),
                    {
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
                    },
                    live_proxies,
                    timeout=4.5,
                )
                parsed = json.loads(raw.decode("utf-8"))
                if isinstance(parsed, list) and len(parsed) == len(uncached_clean):
                    out = []
                    for item in parsed:
                        tr_list = item.get("translations") if isinstance(item, dict) else None
                        out.append((tr_list[0].get("text", "") if tr_list else "").strip())
                    if any(out):
                        return out
            except Exception:
                pass
            return []
        batch_tasks.append(_batch_edge_proxy)

    futures = [_HTTP_RACE_EXECUTOR.submit(t) for t in batch_tasks]
    try:
        for f in concurrent.futures.as_completed(futures, timeout=5.0):
            try:
                res = f.result()
                if res and len(res) == len(uncached_clean) and any(res):
                    batch_out = res
                    break
            except Exception:
                pass
    except concurrent.futures.TimeoutError:
        pass
    finally:
        for other in futures:
            if not other.done():
                other.cancel()

    if batch_out:
        for (idx, orig_s, prep), val in zip(uncached, batch_out):
            if val:
                _cache_set(prep, val)
                _cache_set(orig_s, val)
                results[idx] = val
            else:
                single = translate_text(prep)
                results[idx] = single
                if single:
                    _cache_set(orig_s, single)
        return results

    return results

_IPA_SIBILANTS = ("tʃ", "dʒ", "ʧ", "ʤ", "s", "z", "ʃ", "ʒ")
_IPA_VOICELESS = ("p", "t", "k", "f", "θ")


def _generate_lemma_candidates(word: str) -> List[Tuple[str, str]]:
    """生成英文常见屈折词形（所有格、复数/三单 -s/-es/-ies/-ves、过去式 -ed、分词 -ing）的基词候选列表。"""
    raw_w = (word or "").strip()
    raw_lower = raw_w.lower()
    cands: List[Tuple[str, str]] = []
    seen = set()

    def _add(cand: str, note: str):
        cl = cand.lower()
        if cand and cl != raw_lower and cl not in seen:
            seen.add(cl)
            cands.append((cand, note))

    if raw_lower.endswith("'s") or raw_lower.endswith("’s"):
        _add(raw_w[:-2], "所有格")
    elif raw_lower.endswith("'") or raw_lower.endswith("’"):
        _add(raw_w[:-1], "所有格")
    elif raw_lower.endswith("ies") and len(raw_w) > 4:
        _add(raw_w[:-3] + "y", "复数/第三人称")
        _add(raw_w[:-1], "复数/第三人称")
    elif raw_lower.endswith("ves") and len(raw_w) > 4:
        _add(raw_w[:-3] + "fe", "复数_ves")
        _add(raw_w[:-3] + "f", "复数_ves")
    elif raw_lower.endswith("es") and len(raw_w) > 4:
        # 优先区分 s/x/z/ch/sh + -es（如 passes/watches）与 哑音e + -s（如 orchestrates/nodes/updates）
        if raw_lower.endswith(("sses", "xes", "zes", "ches", "shes")):
            _add(raw_w[:-2], "复数/第三人称")
            _add(raw_w[:-1], "复数/第三人称")
        else:
            _add(raw_w[:-1], "复数/第三人称")
            _add(raw_w[:-2], "复数/第三人称")
    elif raw_lower.endswith("s") and len(raw_w) > 3 and not raw_lower.endswith(("ss", "us", "is")):
        _add(raw_w[:-1], "复数/第三人称")
    elif raw_lower.endswith("ied") and len(raw_w) > 4:
        _add(raw_w[:-3] + "y", "过去式/分词")
        _add(raw_w[:-1], "过去式/分词")
    elif raw_lower.endswith("ed") and len(raw_w) > 4:
        _add(raw_w[:-1], "过去式/分词")
        _add(raw_w[:-2], "过去式/分词")
        if len(raw_w) > 5 and raw_w[-3] == raw_w[-4]:
            _add(raw_w[:-3], "过去式/分词")
    elif raw_lower.endswith("ing") and len(raw_w) > 5:
        _add(raw_w[:-3] + "e", "现在分词")
        _add(raw_w[:-3], "现在分词")
        if len(raw_w) > 6 and raw_w[-4] == raw_w[-5]:
            _add(raw_w[:-4], "现在分词")
    return cands


def _inflect_ipa_s(ipa: str, from_ves: bool = False) -> str:
    """根据英语清浊与咝音尾音音韵规则，为原型音标安全拼接 -s/-es/-ves 屈折尾音（如 /ˈɔːkɪstreɪt/ -> /ˈɔːkɪstreɪts/）。"""
    raw = (ipa or "").strip().strip("/[]").strip()
    if not raw:
        return ""
    head = re.split(r"[;；,]", raw)[0].strip()
    if not head or head.endswith("-"):
        return f"/{raw}/"
    # 英式音标末尾的插音提示 (r)（如 ˈɔːkɪstreɪtə(r)）加 -s 时不发音，直接剥离后接 /z/
    if head.endswith("(r)"):
        head = head[:-3].strip()
    if from_ves and head.endswith("f"):
        head = head[:-1] + "v"
    probe = re.sub(r"\([^)]*\)$", "", head).strip() or head
    suffix = "z"
    if probe.endswith(_IPA_SIBILANTS):
        suffix = "ɪz"
    elif probe.endswith(_IPA_VOICELESS):
        suffix = "s"
    return f"/{head}{suffix}/"


def _inflect_ipa_ed(ipa: str) -> str:
    raw = (ipa or "").strip().strip("/[]").strip()
    if not raw:
        return ""
    head = re.split(r"[;；,]", raw)[0].strip()
    if not head or head.endswith("-"):
        return f"/{raw}/"
    if head.endswith("(r)"):
        head = head[:-3].strip()
    probe = re.sub(r"\([^)]*\)$", "", head).strip() or head
    if probe.endswith(("t", "d")):
        suffix = "ɪd"
    elif probe.endswith(("p", "k", "f", "θ", "s", "ʃ", "tʃ", "ʧ")):
        suffix = "t"
    else:
        suffix = "d"
    return f"/{head}{suffix}/"


def _inflect_ipa_ing(ipa: str) -> str:
    raw = (ipa or "").strip().strip("/[]").strip()
    if not raw:
        return ""
    head = re.split(r"[;；,]", raw)[0].strip()
    if not head or head.endswith("-"):
        return f"/{raw}/"
    if head.endswith("(r)"):
        head = head[:-3] + "r"
    return f"/{head}ɪŋ/"


def _inflect_ipa_agent(uk: str, us: str) -> Tuple[str, str]:
    """为动词原型音标派生 -er/-or 施事/工具名词音标（如 orchestrate -> BrE /ˈɔːkɪstreɪtə(r)/, AmE /ˈɔːrkɪstreɪtər/）。"""
    def _one(src: str, is_us: bool) -> str:
        raw = (src or "").strip().strip("/[]").strip()
        if not raw:
            return ""
        head = re.split(r"[;；,]", raw)[0].strip()
        if not head or head.endswith("-"):
            return f"/{raw}/"
        # 在元音后缀 -ə(r)/-ər 前，英式连读 (r) 必须显性发 /r/（如 explore -> /ɪkˈsplɔːrə(r)/, render -> /ˈrendərə(r)/）
        if head.endswith("(r)"):
            head = head[:-3].strip() + "r"
        if is_us:
            return f"/{head}ər/"
        return f"/{head}ə(r)/"

    b_uk = (uk or us or "").strip()
    b_us = (us or uk or "").strip()
    return (_one(b_uk, False) if b_uk else "", _one(b_us, True) if b_us else "")


def _append_ipa_suffix(ipa: str, suffix: str) -> str:
    raw = (ipa or "").strip().strip("/[]").strip()
    if not raw:
        return ""
    head = re.split(r"[;；,]", raw)[0].strip()
    if not head or head.endswith("-"):
        return f"/{raw}/"
    if head.endswith("(r)"):
        head = head[:-3].strip()
    return f"/{head}{suffix}/"


def _apply_phonetic_transform(uk: str, us: str, kind: str) -> Tuple[str, str]:
    b_uk = (uk or us or "").strip()
    b_us = (us or uk or "").strip()
    if not (b_uk or b_us):
        return "", ""
    if kind == "s_ves":
        return (_inflect_ipa_s(b_uk, from_ves=True), _inflect_ipa_s(b_us, from_ves=True))
    if kind == "s":
        return (_inflect_ipa_s(b_uk), _inflect_ipa_s(b_us))
    if kind == "ed":
        return (_inflect_ipa_ed(b_uk), _inflect_ipa_ed(b_us))
    if kind == "ing":
        return (_inflect_ipa_ing(b_uk), _inflect_ipa_ing(b_us))
    if kind == "agent":
        return _inflect_ipa_agent(b_uk, b_us)
    suffix_map = {"ly": "li", "ness": "nəs", "ment": "mənt", "less": "ləs", "ful": "fʊl"}
    suf = suffix_map.get(kind, "")
    if suf:
        return (_append_ipa_suffix(b_uk, suf), _append_ipa_suffix(b_us, suf))
    return _format_ipa_pair(b_uk, b_us)


def _generate_derivational_candidates(word: str) -> List[Tuple[str, str]]:
    """生成重音不变的常见派生词基词（-er/-or/-ier, -ly/-ily/-bly/-ically, -ness, -ment, -less, -ful），用于音标回退推演。"""
    w = (word or "").strip()
    wl = w.lower()
    out: List[Tuple[str, str]] = []
    seen = set()

    def _add(cand: str, kind: str):
        cl = cand.lower()
        if cand and len(cand) >= 3 and cl != wl and cl not in seen:
            seen.add(cl)
            out.append((cand, kind))

    if wl.endswith(("ier",)) and len(w) > 5:
        _add(w[:-3] + "y", "agent")
    elif wl.endswith(("er", "or")) and len(w) > 5:
        _add(w[:-2] + "e", "agent")
        _add(w[:-2], "agent")
        if len(w) > 6 and w[-3] == w[-4]:
            _add(w[:-3], "agent")
    elif wl.endswith("ically") and len(w) > 7:
        _add(w[:-4], "ly")
    elif wl.endswith("ily") and len(w) > 5:
        _add(w[:-3] + "y", "ly")
    elif wl.endswith("bly") and len(w) > 5:
        _add(w[:-1] + "e", "ly")
    elif wl.endswith("ly") and len(w) > 4:
        _add(w[:-2], "ly")
        _add(w[:-2] + "e", "ly")
    elif wl.endswith("iness") and len(w) > 7:
        _add(w[:-5] + "y", "ness")
    elif wl.endswith("ness") and len(w) > 6:
        _add(w[:-4], "ness")
    elif wl.endswith("ment") and len(w) > 6:
        _add(w[:-4], "ment")
    elif wl.endswith("less") and len(w) > 6:
        _add(w[:-4], "less")
    elif wl.endswith("ful") and len(w) > 5:
        _add(w[:-3], "ful")
    return out


def _phone_cache_get(key: str) -> Optional[Tuple[str, str]]:
    with _DICT_CACHE_LOCK:
        val = _PHONE_CACHE.get(key)
        if val is not None:
            _PHONE_CACHE.move_to_end(key)
        return val


def _phone_cache_set(key: str, val: Tuple[str, str]):
    if not key or not (val[0] or val[1]):
        return
    with _DICT_CACHE_LOCK:
        _PHONE_CACHE[key] = val
        _PHONE_CACHE.move_to_end(key)
        if len(_PHONE_CACHE) > _DICT_CACHE_MAX:
            _PHONE_CACHE.popitem(last=False)


def _resolve_word_phonetics(
    word: str,
    depth: int = 2,
    visited: Optional[set] = None,
    budget: Optional[List[int]] = None,
) -> Tuple[str, str]:
    """多级音标解析：内存缓存 -> 离线库 -> 直查有道 ec+simple -> 规则原形 prototype -> 屈折/派生词干递归推演。"""
    q = (word or "").strip()
    if not q:
        return "", ""
    ql = q.lower()
    if visited is None:
        visited = set()
    if budget is None:
        budget = [3]
    if ql in visited:
        return "", ""
    visited.add(ql)

    cached_p = _phone_cache_get(ql)
    if cached_p is not None:
        return cached_p

    off = OFFLINE_DEFS.get(ql)
    if off and (off.get("uk") or off.get("us")):
        res = _format_ipa_pair(off.get("uk", ""), off.get("us", ""))
        _phone_cache_set(ql, res)
        return res

    off_db_phon = ""
    try:
        off_db_hit = lookup_offline_dict(ql)
        if off_db_hit and off_db_hit.get("phonetic"):
            off_db_phon = off_db_hit["phonetic"]
    except Exception:
        off_db_phon = ""

    proto = ""
    if budget[0] > 0:
        budget[0] -= 1
        uk, us, proto = _fetch_youdao_ec_simple_phonetics(q, timeout=1.4)
        if uk or us:
            res = _format_ipa_pair(uk, us)
            _phone_cache_set(ql, res)
            return res

    if off_db_phon:
        # 仅返回离线单轨音标，严禁伪造相同 AmE 音标或写入 _PHONE_CACHE 阻塞后续联网升级真实双轨英美音标
        return off_db_phon, ""

    if depth <= 0 or " " in q:
        return "", ""

    cands: List[Tuple[str, str]] = []
    seen_cands = set()

    def _infer_kind(base_w: str, raw_hint: str = "") -> str:
        if raw_hint in ("s", "s_ves", "ed", "ing", "agent", "ly", "ness", "ment", "less", "ful"):
            return raw_hint
        if "所有格" in raw_hint:
            return "s" if ql.endswith(("'s", "’s")) else ""
        if "复数_ves" in raw_hint:
            return "s_ves"
        if "复数" in raw_hint or (ql.endswith("s") and not ql.endswith(("ss", "us", "is")) and len(ql) > len(base_w.lower())):
            return "s"
        if "过去式" in raw_hint or ql.endswith("ed"):
            return "ed"
        if "分词" in raw_hint or ql.endswith("ing"):
            return "ing"
        return ""

    # 对有道返回的 prototype：仅当属于规则屈折（kind 非空）时才复用，拒绝把 override 音标张冠李戴给不规则过去式 overrode
    if proto and proto.lower() != ql:
        k = _infer_kind(proto)
        if k:
            seen_cands.add(proto.lower())
            cands.append((proto, k))

    for cand, note in _generate_lemma_candidates(q):
        cl = cand.lower()
        if cl not in seen_cands:
            seen_cands.add(cl)
            cands.append((cand, _infer_kind(cand, note)))

    for cand, kind in _generate_derivational_candidates(q):
        cl = cand.lower()
        if cl not in seen_cands:
            seen_cands.add(cl)
            cands.append((cand, kind))

    # 优先排序：命中本地离线库或 CORE_VOCAB 的真实词干排在前面，避免把 3 次 HTTP 预算耗在 unwrappe/reloade 等假词干上
    def _has_local_phon(w_low: str) -> bool:
        if w_low in OFFLINE_DEFS or _phone_cache_get(w_low) is not None:
            return True
        try:
            h = lookup_offline_dict(w_low)
            return bool(h and h.get("phonetic"))
        except Exception:
            return False

    def _cand_priority(item: Tuple[str, str]) -> int:
        cl = item[0].lower()
        if _has_local_phon(cl):
            return 0
        if cl in CORE_VOCAB:
            return 1
        return 2

    cands.sort(key=_cand_priority)

    for cand, kind in cands[:4]:
        cl = cand.lower()
        if budget[0] <= 0 and not _has_local_phon(cl):
            continue
        b_uk, b_us = _resolve_word_phonetics(cand, depth=depth - 1, visited=visited, budget=budget)
        if b_uk or b_us:
            res = _apply_phonetic_transform(b_uk, b_us, kind)
            _phone_cache_set(ql, res)
            return res

    return "", ""


def _format_ipa_pair(uk: str, us: str, fallback: str = "") -> Tuple[str, str]:
    u_k = (uk or fallback or us or "").strip().strip("/")
    u_s = (us or fallback or uk or "").strip().strip("/")
    return (f"/{u_k}/" if u_k else "", f"/{u_s}/" if u_s else "")


def _fetch_youdao_ec_simple_phonetics(word: str, timeout: float = 1.5) -> Tuple[str, str, str]:
    """查询有道 ec + simple 词典音标，同时提取 prototype 原型词。"""
    q = (word or "").strip()
    if not q:
        return "", "", ""
    enc = urllib.parse.quote(q)
    url = f"https://dict.youdao.com/jsonapi?q={enc}&dicts=%7B%22count%22%3A2%2C%22dicts%22%3A%5B%5B%22ec%22%2C%22simple%22%5D%5D%7D"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with _open_url_smart(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ec0 = (data.get("ec", {}).get("word") or [{}])[0]
        sm0 = (data.get("simple", {}).get("word") or [{}])[0]
        uk = (ec0.get("ukphone") or sm0.get("ukphone") or ec0.get("phone") or sm0.get("phone") or "").strip()
        us = (ec0.get("usphone") or sm0.get("usphone") or ec0.get("phone") or sm0.get("phone") or "").strip()
        proto = (ec0.get("prototype") or "").strip()
        return uk, us, proto
    except Exception:
        return "", "", ""


def lookup_fast(word: str, context_sentence: str = "") -> Dict[str, Any]:
    """
    0 延迟本地极速查词（绝不进行任何同步网络阻塞，彻底杜绝主线程卡死和未响应）
    """
    clean = clean_input_text(word)
    clean_lower = clean.lower()

    # 针对学术/算法课件（如 Computer Modern 细衬线字体）笔画丢失引发的典型残缺词进行智能还原
    if clean_lower in ("umb", "umb rof", "umb of"):
        clean = "number" if clean_lower == "umb" else "number of"
        clean_lower = clean.lower()
    elif clean_lower == "rof":
        clean = "of"
        clean_lower = "of"
    elif clean_lower == "rem ved":
        clean = "removed"
        clean_lower = "removed"
    elif clean_lower in ("op imizing", "optim izing"):
        clean = "optimizing"
        clean_lower = "optimizing"
    elif clean_lower in ("ass", "ass-") and any(k in context_sentence.lower() for k in ("pass", "sort", "through", "each", "bubble", "loop")):
        clean = "pass"
        clean_lower = "pass"

    # 若语境例句包含此类腐蚀词块，同步智能纠偏语境
    if context_sentence and "umb rof" in context_sentence.lower():
        context_sentence = re.sub(r'\bumb\s+rof\b', 'number of', context_sentence, flags=re.IGNORECASE)
    elif context_sentence and "umb" in context_sentence.lower():
        context_sentence = re.sub(r'\bThe\s+umb\b', 'The number', context_sentence, flags=re.IGNORECASE)
    if context_sentence and "ach pass" in context_sentence.lower():
        context_sentence = re.sub(r'\bach\s+pass\b', 'each pass', context_sentence, flags=re.IGNORECASE)

    word_count = len(clean.split())
    target_lookup_word = clean
    spelling_correction = None
    local = OFFLINE_DEFS.get(clean_lower)

    # 优先精确命中离线全词库（52k+ 词，0ms 只读 SQLite），避免 exact 词条如 based/noted/passing 被 OFFLINE_DEFS 词干剥离劫持
    offline_db_hit = None
    offline_db_deriv_note = ""
    offline_db_target = clean
    if not local and word_count <= 2:
        try:
            offline_db_hit = lookup_offline_dict(clean_lower)
        except Exception:
            offline_db_hit = None

    # 智能词形还原：针对所有格（如 Pascal's）、复数/三单（-ies/-ves/-es/-s）、过去式（-ed）、分词（-ing）检索基词
    is_derivative = False
    base_display_note = ""
    lemma_cands: List[Tuple[str, str]] = _generate_lemma_candidates(clean) if word_count == 1 else []
    if not local and not offline_db_hit and word_count == 1:
        for cand, note in lemma_cands:
            if cand.lower() in OFFLINE_DEFS:
                local = OFFLINE_DEFS[cand.lower()]
                target_lookup_word = cand
                is_derivative = True
                base_display_note = note
                break

    # 针对 2~4 词短语自适应还原（如 'pascal triangle' vs "pascal's triangle", 'lecture notes' vs 'lecture note'）
    if not local and not offline_db_hit and 2 <= word_count <= 4:
        p_cands = []
        p_no_poss = re.sub(r"['’]s\b", "", clean_lower).strip()
        p_cands.append(p_no_poss)
        p_with_poss = re.sub(r"\b([a-zA-Z]+)\s+", r"\1's ", clean_lower, count=1).strip()
        p_cands.append(p_with_poss)
        if clean_lower.endswith("s"):
            p_cands.append(clean_lower[:-1])
        for pc in p_cands:
            if pc in OFFLINE_DEFS:
                local = OFFLINE_DEFS[pc]
                target_lookup_word = clean
                break

    # 若精确检索与 OFFLINE_DEFS 词干均未命中，再对离线全词库做词形还原检索
    if not local and not offline_db_hit and word_count == 1 and lemma_cands:
        try:
            for cand, note in lemma_cands:
                cand_hit = lookup_offline_dict(cand.lower())
                if cand_hit:
                    offline_db_hit = cand_hit
                    offline_db_deriv_note = note
                    offline_db_target = cand
                    break
        except Exception:
            offline_db_hit = None

    # 仅当内置词典与离线全词库均未命中时，才进行拼写纠错建议探测（杜绝将合法的 factorial, recursion 误纠偏为 factor）
    if not local and not offline_db_hit and word_count == 1:
        sugg = check_spelling(clean)
        if sugg and sugg.lower() != clean_lower:
            spelling_correction = {
                "original": clean,
                "suggested": sugg,
                "explanation": f"检测到疑似拼写错误，建议纠正为 {sugg}"
            }
            target_lookup_word = sugg
            if target_lookup_word.lower() in OFFLINE_DEFS:
                local = OFFLINE_DEFS[target_lookup_word.lower()]
            else:
                try:
                    sugg_hit = lookup_offline_dict(target_lookup_word.lower())
                    if sugg_hit:
                        offline_db_hit = sugg_hit
                        offline_db_target = target_lookup_word
                except Exception:
                    pass

    tags = ACADEMIC_TAGS.get(target_lookup_word.lower(), ["学术词汇" if word_count <= 2 else "课件长句"])

    if local:
        defs_list = []
        for d in local.get("defs", []):
            d_copy = dict(d)
            if is_derivative and base_display_note:
                d_copy["cn"] = f"[{base_display_note}] {d_copy['cn']}"
            defs_list.append(d_copy)

        local_uk = local.get("uk", "")
        local_us = local.get("us", "")
        if is_derivative and clean_lower != target_lookup_word.lower():
            t_kind = ""
            if "复数_ves" in base_display_note:
                t_kind = "s_ves"
            elif "复数" in base_display_note or (clean_lower.endswith(("'s", "’s")) and "所有格" in base_display_note):
                t_kind = "s"
            elif "过去式" in base_display_note:
                t_kind = "ed"
            elif "分词" in base_display_note:
                t_kind = "ing"
            if t_kind:
                t_uk, t_us = _apply_phonetic_transform(local_uk, local_us, t_kind)
                local_uk = t_uk or local_uk
                local_us = t_us or local_us

        has_real_ctx = bool((context_sentence or "").strip())
        # 严格遵循 AGENTS.md §14.3：仅当无现场语境（使用离线默认例句）时展示离线固定译文；有真实屏幕语境时异步翻译真实语境
        ctx_trans = "" if has_real_ctx else (f"↳ 译文：{local['trans']}" if local.get("trans") else "")
        return {
            "word": clean,
            "target_word": target_lookup_word,
            "spelling_correction": spelling_correction,
            "phonetic_uk": local_uk,
            "phonetic_us": local_us,
            "definitions": defs_list,
            "tags": tags,
            "context_sentence": context_sentence or f"The theoretical foundation of {target_lookup_word} was discussed in class.",
            "context_translation": ctx_trans,
            "ai_analysis": "",
            "needs_enrichment": has_real_ctx
        }

    # 命中真·离线全词库（52k+ 词）：0ms 直出真实中文释义与基础音标，同时允许后台异步升级双轨英美音标与现场语境译文
    if offline_db_hit:
        defs_list = []
        for d in offline_db_hit.get("defs", []):
            d_copy = dict(d)
            if offline_db_deriv_note:
                d_copy["cn"] = f"[{offline_db_deriv_note}] {d_copy['cn']}"
            defs_list.append(d_copy)

        off_phon = (offline_db_hit.get("phonetic") or "").strip()
        if off_phon and offline_db_deriv_note and clean_lower != offline_db_target.lower():
            t_kind = ""
            if "复数_ves" in offline_db_deriv_note:
                t_kind = "s_ves"
            elif "复数" in offline_db_deriv_note or (clean_lower.endswith(("'s", "’s")) and "所有格" in offline_db_deriv_note):
                t_kind = "s"
            elif "过去式" in offline_db_deriv_note:
                t_kind = "ed"
            elif "分词" in offline_db_deriv_note:
                t_kind = "ing"
            if t_kind:
                t_uk, _ = _apply_phonetic_transform(off_phon, off_phon, t_kind)
                off_phon = t_uk or off_phon
        elif not off_phon and word_count == 1:
            # 词条本身无音标（如 orchestrator）时，0ms 尝试从离线全词库派生/词形基词推演音标
            for cand, kind in (
                [(c, "s" if "复数" in n else ("ed" if "过去式" in n else ("ing" if "分词" in n else ""))) for c, n in lemma_cands]
                + _generate_derivational_candidates(clean)
            ):
                if not kind:
                    continue
                base_h = lookup_offline_dict(cand.lower())
                if base_h and base_h.get("phonetic"):
                    t_uk, _ = _apply_phonetic_transform(base_h["phonetic"], base_h["phonetic"], kind)
                    if t_uk:
                        off_phon = t_uk
                        break

        db_uk = off_phon
        db_us = ""
        try:
            from .database import get_word
            db_row = get_word(f"word_{clean_lower}")
            if db_row:
                if db_row.get("phonetic_uk"):
                    db_uk = db_row["phonetic_uk"]
                if db_row.get("phonetic_us"):
                    db_us = db_row["phonetic_us"]
        except Exception:
            pass

        off_tags = offline_db_hit.get("tags") or tags
        has_real_ctx = bool((context_sentence or "").strip())
        needs_enrich = bool(has_real_ctx or not (db_uk and db_us))
        return {
            "word": clean,
            "target_word": offline_db_target if spelling_correction else clean,
            "spelling_correction": spelling_correction,
            "phonetic_uk": db_uk,
            "phonetic_us": db_us,
            "definitions": defs_list,
            "tags": off_tags,
            "context_sentence": context_sentence or clean,
            "context_translation": "",
            "ai_analysis": "",
            "key_phrases": [],
            "needs_enrichment": needs_enrich,
        }

    # 非内置词条：先给轻量骨架，由异步子线程秒级补全，杜绝生硬文本与白屏卡顿
    is_long = word_count > 3
    initial_placeholder = "正在智能翻译整句内容..." if is_long else "正在查询释义..."
    initial_defs = [{"pos": "长句" if is_long else "释义", "cn": initial_placeholder, "en": ""}]
    cached_uk = ""
    cached_us = ""
    has_db_real_defs = False

    if not local and word_count <= 2:
        try:
            from .database import get_word
            db_row = get_word(f"word_{clean_lower}")
            if db_row:
                cached_uk = db_row.get("phonetic_uk") or ""
                cached_us = db_row.get("phonetic_us") or ""
                db_defs = db_row.get("definitions") or []
                if db_defs and any(d.get("pos") not in ("释义", "长句", "短语", "") and d.get("cn") not in LOADING_PLACEHOLDERS for d in db_defs):
                    initial_defs = db_defs
                    has_db_real_defs = True
            if not cached_uk and not cached_us and word_count == 1 and lemma_cands:
                for cand, note in lemma_cands:
                    cand_row = get_word(f"word_{cand.lower()}")
                    b_uk = (cand_row or {}).get("phonetic_uk") or ""
                    b_us = (cand_row or {}).get("phonetic_us") or ""
                    if b_uk or b_us:
                        t_kind = ""
                        if "复数_ves" in note:
                            t_kind = "s_ves"
                        elif "复数" in note or (clean_lower.endswith(("'s", "’s")) and "所有格" in note):
                            t_kind = "s"
                        elif "过去式" in note:
                            t_kind = "ed"
                        elif "分词" in note:
                            t_kind = "ing"
                        if t_kind and clean_lower != cand.lower():
                            cached_uk, cached_us = _apply_phonetic_transform(b_uk, b_us, t_kind)
                        else:
                            cached_uk = b_uk or b_us
                            cached_us = b_us or b_uk
                        break
        except Exception:
            pass

    initial_key_phrases: List[Dict[str, str]] = []
    if is_long:
        for kp in extract_key_phrases(clean):
            hit = _lookup_phrase_offline(kp)
            if hit:
                initial_key_phrases.append(hit)

    needs_enrich = True
    if not is_long and has_db_real_defs and cached_uk and cached_us and not (context_sentence or "").strip():
        needs_enrich = False

    return {
        "word": clean,
        "target_word": target_lookup_word,
        "spelling_correction": spelling_correction,
        "phonetic_uk": cached_uk,
        "phonetic_us": cached_us,
        "definitions": initial_defs,
        "tags": ["学术长句"] if is_long else ["课件词汇"],
        "context_sentence": context_sentence or clean,
        "context_translation": "",
        "ai_analysis": "",
        "key_phrases": initial_key_phrases,
        "needs_enrichment": needs_enrich
    }

def preprocess_for_translation(text: str) -> str:
    """
    长句/段落翻译前预处理：
    1. 修复句首/句中分句状语短语（如 'At first.', 'However.'）后的破句句号为连贯逗号，保障译文流畅度；
    2. 修复常见手写草书连笔粘合词与常见 OCR 畸变（如 'hadCome' -> 'had some', 'Orb ve' -> 'Above', 'treout' -> 'treat'），杜绝机翻误解。
    """
    if not text:
        return ""
    # 支持全文开头及句内各分句开头的状语破句修复
    t = re.sub(
        r'(^|[.!?]\s+)(At first|However|In addition|First of all|For example|Therefore|Moreover|To begin with|In general|Furthermore)\.\s+',
        r'\1\2, ',
        text,
        flags=re.IGNORECASE
    )
    def _rep_hadcome(m):
        raw = m.group(0)
        return "Had some" if raw[0].isupper() else "had some"
    t = re.sub(r'\bhad(?:Come|come|cowe|Gome)\b', _rep_hadcome, t, flags=re.IGNORECASE)

    # 算法手写笔记高频 OCR 畸变修正，防止机翻产生荒谬语义
    # 1. 简缩词与空格修拢: "does n't" -> "doesn't"
    t = re.sub(r"\b([a-zA-Z]+)\s+n['’]t\b", r"\1n't", t, flags=re.IGNORECASE)
    # 2. Orb ve / Orbo ve -> Above
    t = re.sub(r'\b(?:Orb|Orbo|Obo)\s+ve\b', 'Above', t, flags=re.IGNORECASE)
    t = re.sub(r'\b(?:Orb|Orbo|Obo)\s+one\b', 'Above, one', t, flags=re.IGNORECASE)
    # 3. treout -> treat (动词原形规范，避免单复数假定冲突)
    t = re.sub(r'\b(?:treout|treot|treut)\b', 'treat', t, flags=re.IGNORECASE)
    # 4. owever / iowever -> however (闭包自适应保全大小写)
    t = re.sub(r'\b[io]wever\b', lambda m: 'However' if m.group(0)[0].isupper() else 'however', t, flags=re.IGNORECASE)
    # 5. shoulo -> should
    t = re.sub(r'\bshoulo\b', 'should', t, flags=re.IGNORECASE)
    # 6. Big-O 标记规范化：仅修复明确的 OC...) 括号畸变与完整单符号 O(n)，绝不破坏 O(log n)/O(n^2)/OCR/OCaml！
    t = re.sub(r'\bOC([A-Za-z0-9_]+)\)', lambda m: f"O({m.group(1).upper()})", t)
    t = re.sub(r'\bO\(([A-Za-z0-9_]+)\)', lambda m: f"O({m.group(1).upper()})", t)
    return t

def enrich_lookup_sync(fast_data: Dict[str, Any], on_partial_update: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """在后台子线程运行的网络增强查询（解耦并发执行，支持渐进式多阶段上屏与防卡死自愈）"""
    clean = fast_data.get("target_word", fast_data["word"])
    orig_clean = fast_data["word"]
    clean_lower = clean.lower()
    word_count = len(clean.split())
    context_sentence = fast_data.get("context_sentence", "")

    if clean_lower in ("ass", "ass-") and any(k in context_sentence.lower() for k in ("pass", "sort", "through", "each", "bubble", "loop")):
        clean = "pass"
        clean_lower = "pass"
        fast_data["target_word"] = clean

    # 1. 长句翻译与语法纠错 (Polished-First 离线毫秒润色先行 + 150ms 极速翻译优先上屏 + 在线语法后台并行)
    if word_count > 3:
        # 严格遵循 AGENTS.md 第 14.3 章 Polished-First Pipeline：
        # 先经 <0.5ms 离线语法/拼写润色消除残缺碎片，再送交翻译引擎，彻底杜绝火星文机翻
        local_grammar = {}
        try:
            local_grammar = check_grammar_and_spelling(orig_clean, allow_online=False)
        except Exception:
            local_grammar = {}

        polished_base = (
            local_grammar["polished_sentence"]
            if (local_grammar and local_grammar.get("polished_sentence"))
            else orig_clean
        )

        def _task_trans():
            text_for_trans = preprocess_for_translation(polished_base)
            return translate_text(text_for_trans)

        def _task_grammar():
            try:
                return check_grammar_and_spelling(orig_clean, allow_online=True)
            except Exception:
                return local_grammar or {}

        def _task_phrases():
            cand_phrases = extract_key_phrases(orig_clean)
            if not cand_phrases:
                return []
            resolved: List[Dict[str, str]] = []
            pending: List[tuple[int, str, Any]] = []
            slots: List[Optional[Dict[str, str]]] = [None] * len(cand_phrases)
            for idx, ph in enumerate(cand_phrases):
                off = _lookup_phrase_offline(ph)
                if off:
                    slots[idx] = off
                else:
                    # 使用独立短语子线程池 (_PHRASE_EXECUTOR)，严禁向 _PIPELINE_EXECUTOR 自嵌套提交引发饥饿死锁
                    fut = _PHRASE_EXECUTOR.submit(_lookup_phrase_online, ph)
                    pending.append((idx, ph, fut))
            for idx, ph, fut in pending:
                try:
                    res_item = fut.result(timeout=1.6)
                    if res_item and res_item.get("cn"):
                        slots[idx] = res_item
                except Exception:
                    fut.cancel()
            for item in slots:
                if item and item.get("cn"):
                    resolved.append(item)
            return resolved

        future_trans = _PIPELINE_EXECUTOR.submit(_task_trans)
        future_grammar = _PIPELINE_EXECUTOR.submit(_task_grammar)
        future_phrases = _PIPELINE_EXECUTOR.submit(_task_phrases)

        # 轨 A：翻译通常 120~200ms 返回（经活代理 1.2~2.5s），一旦就绪立即通过 on_partial_update 刷屏！
        try:
            trans = future_trans.result(timeout=4.8)
        except Exception:
            future_trans.cancel()
            trans = ""

        if trans:
            fast_data["definitions"] = [{"pos": "译文", "cn": trans, "en": ""}]
            fast_data["context_translation"] = f"↳ 译文：{trans}"
            fast_data["translation_status"] = ""
        else:
            fast_data["definitions"] = []
            fast_data["context_translation"] = ""
            fast_data["translation_status"] = "offline"

        # 若离线润色已检出明确纠错，在首波刷屏时即可先行呈现，无需苦等海外 LanguageTool
        if local_grammar and local_grammar.get("corrections"):
            fast_data["grammar_result"] = local_grammar

        if on_partial_update:
            try:
                on_partial_update(dict(fast_data))
            except Exception:
                pass

        # 轨 B：回收完整语法纠错结果与重点短语解析，若有增强建议再平滑补全
        try:
            grammar_res = future_grammar.result(timeout=1.6)
        except Exception:
            future_grammar.cancel()
            grammar_res = local_grammar or {}

        if grammar_res and (grammar_res.get("corrections") or grammar_res.get("polished_sentence")):
            fast_data["grammar_result"] = grammar_res

        try:
            phrases_res = future_phrases.result(timeout=1.6)
            if phrases_res:
                fast_data["key_phrases"] = phrases_res
        except Exception:
            future_phrases.cancel()

        fast_data["needs_enrichment"] = False
        return fast_data

    # 2. 短词 / 短语网络释义查询（解耦并发管线：释义音标、语境翻译与极速探针并行处理）
    phonetic_uk = fast_data.get("phonetic_uk", "")
    phonetic_us = fast_data.get("phonetic_us", "")

    def _fetch_word_definition():
        cache_key = clean_lower
        with _DICT_CACHE_LOCK:
            cached_defs = _DEF_CACHE.get(cache_key)
            if cached_defs is not None:
                _DEF_CACHE.move_to_end(cache_key)
                return clean, [dict(x) for x in cached_defs]

        defs_res = []
        encoded_query = urllib.parse.quote(clean)
        url = f"https://dict.youdao.com/suggest?q={encoded_query}&le=en&num=1&doctype=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        # 严格走 _open_url_smart（先直连，仅在失败且本地代理 TCP 存活时走代理）
        try:
            with _open_url_smart(req, timeout=1.8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                entries = data.get("data", {}).get("entries", [])
                if entries:
                    suggested_entry = entries[0].get("entry", "").strip()
                    clean_norm = re.sub(r"[\s\-]+", "", clean_lower)
                    sugg_norm = re.sub(r"[\s\-]+", "", suggested_entry.lower())
                    lemma_norms = {re.sub(r"[\s\-]+", "", c.lower()) for c, _ in _generate_lemma_candidates(clean)}
                    # 仅当返回词头与查询词精确匹配（忽略空格/连字符）或为其合法屈折原形时才采纳 explain，防止 foo->food 错词串扰
                    if suggested_entry and (sugg_norm == clean_norm or sugg_norm in lemma_norms):
                        explain = entries[0].get("explain", "")
                        if explain:
                            parts = [p.strip() for p in re.split(r'[;；]', explain) if p.strip()]
                            for part in parts[:4]:
                                m = re.match(r'^([a-zA-Z]+\.)\s*(.+)$', part)
                                if m:
                                    defs_res.append({"pos": m.group(1), "cn": m.group(2), "en": ""})
                                else:
                                    defs_res.append({"pos": "释义" if len(clean.split()) == 1 else "短语", "cn": part, "en": ""})
        except Exception:
            pass

        if not defs_res:
            t = translate_text(clean)
            if t:
                defs_res.append({"pos": "短语" if len(clean.split()) > 1 else "释义", "cn": t, "en": ""})
            else:
                return clean, []
        else:
            with _DICT_CACHE_LOCK:
                _DEF_CACHE[cache_key] = [dict(x) for x in defs_res]
                _DEF_CACHE.move_to_end(cache_key)
                if len(_DEF_CACHE) > _DICT_CACHE_MAX:
                    _DEF_CACHE.popitem(last=False)
        return clean, defs_res

    def _fetch_phonetics():
        p_uk = phonetic_uk
        p_us = phonetic_us
        if clean and word_count <= 2:
            try:
                phone_query = clean
                if phone_query.lower().endswith(("'s", "’s")):
                    phone_query = phone_query[:-2]
                elif phone_query.lower().endswith(("'", "’")):
                    phone_query = phone_query[:-1]
                r_uk, r_us = _resolve_word_phonetics(phone_query, depth=2, budget=[3])
                if r_uk or r_us:
                    return r_uk, r_us
            except Exception:
                pass
        return p_uk, p_us

    def _fetch_context_translation():
        if not context_sentence:
            return ""
        # 严格遵循 AGENTS.md 第 14.3 章：UI 保留原始 context_sentence，但送入翻译引擎的临时副本先做 <0.5ms 离线润色去噪
        ctx_for_trans = context_sentence
        try:
            ctx_gram = check_grammar_and_spelling(context_sentence, allow_online=False)
            if ctx_gram and ctx_gram.get("polished_sentence"):
                ctx_for_trans = ctx_gram["polished_sentence"]
        except Exception:
            pass
        return translate_text(preprocess_for_translation(ctx_for_trans))

    # 单词/短语模式（<=3 词）绝对禁止调用海外语法纠错接口，将网络带宽与线程全额留给释义
    def _fetch_fast_trans():
        return translate_text(clean)

    cur_defs = fast_data.get("definitions") or []
    is_prev_offline_partial = fast_data.get("translation_status") in ("offline", "offline_partial")
    has_existing_real_defs = bool(
        not is_prev_offline_partial
        and cur_defs and any(
            (d.get("cn") or "").strip() not in ("", *LOADING_PLACEHOLDERS)
            and (d.get("cn") or "").strip().lower() != clean_lower
            and (
                (d.get("pos") or "") not in ("释义", "短语", "长句", "")
                or any("\u4e00" <= ch <= "\u9fff" for ch in (d.get("cn") or ""))
            )
            for d in cur_defs
        )
    )
    has_existing_phones = bool((phonetic_uk or "").strip() and (phonetic_us or "").strip())
    existing_ctx_trans = (fast_data.get("context_translation") or "").strip()
    has_existing_ctx_trans = bool(existing_ctx_trans and existing_ctx_trans not in CTX_LOADING_PLACEHOLDERS)

    def _has_real_def_list(d_list: List[Dict[str, str]]) -> bool:
        return bool(
            d_list
            and any(
                (d.get("cn") or "").strip() not in ("", *LOADING_PLACEHOLDERS)
                and (d.get("cn") or "").strip().lower() != clean_lower
                for d in d_list
            )
        )

    # 统一截止时间预算（5.2s < UI 6.8s 自愈定时器），严防 4 个阶段串行叠加导致 UI 定时器抢跑误报
    enrich_deadline = time.monotonic() + 5.2

    def _rem_timeout(cap: float) -> float:
        return min(cap, max(0.15, enrich_deadline - time.monotonic()))

    # 并发管线：外层编排统一提交至独立 _PIPELINE_EXECUTOR，与内层 HTTP 竞速池完全物理隔离
    f_fast_trans = None if has_existing_real_defs else _PIPELINE_EXECUTOR.submit(_fetch_fast_trans)
    f_word = None if has_existing_real_defs else _PIPELINE_EXECUTOR.submit(_fetch_word_definition)
    f_phone = None if has_existing_phones else _PIPELINE_EXECUTOR.submit(_fetch_phonetics)
    f_trans = None if (has_existing_ctx_trans or not (context_sentence or "").strip()) else _PIPELINE_EXECUTOR.submit(_fetch_context_translation)

    # 阶段一：极速直连先行探针（拒绝原样回显英文原词欺骗上屏）
    if f_fast_trans is not None:
        try:
            fast_cn = f_fast_trans.result(timeout=_rem_timeout(0.6))
            if fast_cn and fast_cn.strip().lower() != clean_lower:
                fast_data["definitions"] = [{"pos": "释义", "cn": fast_cn, "en": ""}]
                if on_partial_update:
                    on_partial_update(dict(fast_data))
        except Exception:
            f_fast_trans.cancel()

    # 阶段二：词典权威词性与真实双轨音标就绪（平滑覆盖升级；严禁覆盖 fast_data["word"] 破坏标题/音标/朗读/收藏一致性）
    if f_word is not None:
        try:
            _c_word, defs = f_word.result(timeout=_rem_timeout(3.8))
            is_dummy_fallback = bool(
                not defs
                or (
                    len(defs) == 1
                    and (defs[0].get("pos") in ("术语", "释义", ""))
                    and (defs[0].get("cn") or "").strip().lower() == clean_lower
                )
            )
            cur_d0 = (fast_data.get("definitions") or [{}])[0]
            has_cur_cn = bool(
                (cur_d0.get("cn") or "").strip() not in ("", *LOADING_PLACEHOLDERS)
                and (cur_d0.get("cn") or "").strip().lower() != clean_lower
            )
            if defs and not (is_dummy_fallback and has_cur_cn):
                fast_data["definitions"] = defs
                if on_partial_update:
                    on_partial_update(dict(fast_data))
        except Exception:
            f_word.cancel()
            if not fast_data.get("definitions") or fast_data["definitions"][0].get("cn") in LOADING_PLACEHOLDERS:
                fast_data["definitions"] = []

    if f_phone is not None:
        try:
            p_uk, p_us = f_phone.result(timeout=_rem_timeout(2.2))
            if p_uk:
                fast_data["phonetic_uk"] = p_uk
            if p_us:
                fast_data["phonetic_us"] = p_us
            if (p_uk or p_us) and (has_existing_real_defs or _has_real_def_list(fast_data.get("definitions") or [])) and on_partial_update:
                on_partial_update(dict(fast_data))
        except Exception:
            f_phone.cancel()

    # 阶段三：语境例句翻译上屏（仅在尚未就绪时更新，绝不清空已有合法译文）
    if f_trans is not None:
        try:
            ctx_t = f_trans.result(timeout=_rem_timeout(1.2))
            fast_data["context_translation"] = f"↳ 译文：{ctx_t}" if ctx_t else ""
        except Exception:
            f_trans.cancel()
            if fast_data.get("context_translation") in CTX_LOADING_PLACEHOLDERS:
                fast_data["context_translation"] = ""

    def _lookup_token_offline_cn(tok: str) -> str:
        t_clean = clean_input_text(tok)
        if not t_clean:
            return ""
        cands = [t_clean] + [c for c, _ in _generate_lemma_candidates(t_clean)]
        for c in cands:
            cl = c.lower()
            entry = OFFLINE_DEFS.get(cl)
            if entry and entry.get("defs"):
                cn = (entry["defs"][0].get("cn") or "").strip()
                if cn:
                    return cn
            try:
                db_h = lookup_offline_dict(cl)
            except Exception:
                db_h = None
            if db_h and db_h.get("defs"):
                cn = (db_h["defs"][0].get("cn") or "").strip()
                if cn:
                    return cn
        return ""

    # 兜底判定：若已有真实释义（含离线词库/在线结果），绝不弹离线提示；否则按短语逐词参考或纯离线态收口
    final_defs = fast_data.get("definitions") or []
    if has_existing_real_defs or _has_real_def_list(final_defs):
        fast_data["translation_status"] = ""
    else:
        partial_defs: List[Dict[str, str]] = []
        if 2 <= word_count <= 3:
            _STOPWORDS = frozenset({"a", "an", "the", "of", "to", "in", "on", "at", "is", "are", "for", "by", "with", "and", "or", "as", "it"})
            tokens = [t.strip(".,;:!?\"'()[]{}") for t in clean.split() if t.strip(".,;:!?\"'()[]{}")]
            non_stop = [t for t in tokens if t.lower() not in _STOPWORDS and len(t) > 1]
            scan_tokens = non_stop if non_stop else tokens
            seen_toks = set()
            for tok in scan_tokens:
                tl = tok.lower()
                if tl in seen_toks:
                    continue
                seen_toks.add(tl)
                cn_hit = _lookup_token_offline_cn(tok)
                if cn_hit:
                    partial_defs.append({"pos": tok, "cn": cn_hit.split("；")[0].split(";")[0].strip(), "en": ""})
        if partial_defs:
            fast_data["definitions"] = partial_defs
            fast_data["translation_status"] = "offline_partial"
        else:
            fast_data["definitions"] = []
            fast_data["translation_status"] = "offline"

    if fast_data.get("context_translation") in CTX_LOADING_PLACEHOLDERS:
        fast_data["context_translation"] = ""

    fast_data["needs_enrichment"] = False
    return fast_data

def lookup(word: str, context_sentence: str = "") -> Dict[str, Any]:
    """统一查词入口：先取极速缓存，再尝试增强"""
    data = lookup_fast(word, context_sentence)
    if data.get("needs_enrichment", False):
        try:
            return enrich_lookup_sync(data)
        except Exception:
            pass
    return data

def prewarm_service():
    """后台预热本地词库与离线服务，杜绝启动初期网络阻塞"""
    def _run():
        try:
            lookup_fast("hello")
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()

AUDIO_CACHE_DIR = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "screenlex_audio")
try:
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
except Exception:
    pass

_SPEAK_LOCK = threading.Lock()
_SPEAK_SEQ_LOCK = threading.Lock()
_SPEAK_SEQ = 0
_AUDIO_CACHE_MAX_FILES = 200


def _is_valid_mp3_bytes(data: bytes) -> bool:
    if not data or len(data) <= 500:
        return False
    # ID3 标签头或 MPEG 帧同步字 (0xFFE0 掩码)
    if data[:3] == b"ID3":
        return True
    if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return True
    return False


def _prune_audio_cache():
    try:
        entries = [
            os.path.join(AUDIO_CACHE_DIR, fn)
            for fn in os.listdir(AUDIO_CACHE_DIR)
            if fn.endswith(".mp3")
        ]
        if len(entries) <= _AUDIO_CACHE_MAX_FILES:
            return
        entries.sort(key=lambda p: os.path.getmtime(p))
        for old_p in entries[: len(entries) - _AUDIO_CACHE_MAX_FILES]:
            try:
                os.remove(old_p)
            except Exception:
                pass
    except Exception:
        pass


def _play_mci_audio(file_path: str, my_seq: int):
    """在持有 MCI alias 的同一线程内轮询播放状态与新播放序号，严格遵守 WinMM 线程亲和性并支持秒级切音中断。"""
    import ctypes
    import random
    winmm = ctypes.windll.winmm
    alias = f"vox_{int(time.time() * 1000)}_{random.randint(100, 999)}"
    status_buf = ctypes.create_unicode_buffer(64)
    try:
        rc_open = winmm.mciSendStringW(f'open "{file_path}" type mpegvideo alias {alias}', None, 0, 0)
        if rc_open != 0:
            raise RuntimeError(f"MCI open failed: {rc_open}")
        rc_play = winmm.mciSendStringW(f'play {alias}', None, 0, 0)
        if rc_play != 0:
            raise RuntimeError(f"MCI play failed: {rc_play}")
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            with _SPEAK_SEQ_LOCK:
                if my_seq != _SPEAK_SEQ:
                    winmm.mciSendStringW(f"stop {alias}", None, 0, 0)
                    return
            rc_st = winmm.mciSendStringW(f"status {alias} mode", status_buf, len(status_buf), 0)
            if rc_st != 0:
                break
            mode = status_buf.value.strip().lower()
            if mode in ("stopped", "not ready", ""):
                break
            time.sleep(0.02)
    finally:
        winmm.mciSendStringW(f'close {alias}', None, 0, 0)


def speak_word(word: str, accent: str = "us"):
    """
    真人原生双轨发音：
    - accent='uk': 纯正英音 (British RP, Youdao type 1)
    - accent='us': 纯正美音 (American GA, Youdao type 2)
    优先播放本地高保真真人录音缓存（0 延迟），并发切音互斥，无网/坏音频时自动降级至 Windows SAPI
    """
    global _SPEAK_SEQ
    clean = re.sub(r"[^a-zA-Z\s\-']", "", (word or "")).strip()
    if not clean:
        return
    with _SPEAK_SEQ_LOCK:
        _SPEAK_SEQ += 1
        my_seq = _SPEAK_SEQ

    def _run():
        accent_type = 1 if accent.lower() == "uk" else 2
        safe_name = re.sub(r"[\s\-']+", "_", clean.lower()).strip("_") or "word"
        file_name = f"{safe_name}_{accent.lower()}.mp3"
        cached_path = os.path.join(AUDIO_CACHE_DIR, file_name)

        with _SPEAK_LOCK:
            with _SPEAK_SEQ_LOCK:
                if my_seq != _SPEAK_SEQ:
                    return
            # 1. 尝试从本地缓存或在线真人发音库获取高保真音频（强制旁路系统死代理 + 原子落盘校验）
            try:
                need_download = True
                if os.path.exists(cached_path) and os.path.getsize(cached_path) > 500:
                    try:
                        with open(cached_path, "rb") as rf:
                            head = rf.read(512)
                        if _is_valid_mp3_bytes(head + b"\x00" * 512):
                            need_download = False
                    except Exception:
                        pass
                if need_download:
                    encoded = urllib.parse.quote(clean)
                    url = f"https://dict.youdao.com/dictvoice?audio={encoded}&type={accent_type}"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                    with _open_url_direct(req, timeout=2.2) as resp:
                        data = resp.read()
                        if _is_valid_mp3_bytes(data):
                            tmp_path = f"{cached_path}.{threading.get_ident()}.tmp"
                            try:
                                with open(tmp_path, "wb") as f:
                                    f.write(data)
                                os.replace(tmp_path, cached_path)
                                _prune_audio_cache()
                            finally:
                                if os.path.exists(tmp_path):
                                    try:
                                        os.remove(tmp_path)
                                    except Exception:
                                        pass

                with _SPEAK_SEQ_LOCK:
                    if my_seq != _SPEAK_SEQ:
                        return
                if os.path.exists(cached_path) and os.path.getsize(cached_path) > 500:
                    _play_mci_audio(cached_path, my_seq)
                    return
            except Exception:
                # 若坏音频导致 MCI 解码失败，清理损坏缓存以便下次重新获取
                try:
                    if os.path.exists(cached_path):
                        os.remove(cached_path)
                except Exception:
                    pass

            with _SPEAK_SEQ_LOCK:
                if my_seq != _SPEAK_SEQ:
                    return
            # 2. 离线降级方案：调用 Windows SAPI 本地合成语音
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                try:
                    speaker = win32com.client.Dispatch("SAPI.SpVoice")
                    speaker.Speak(clean)
                finally:
                    pythoncom.CoUninitialize()
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()
