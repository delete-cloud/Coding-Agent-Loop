# 这次 Copilot review/fix 的复盘

## 结论

这次最重要的收获不是某一个 bug，而是：**review comment 只是一条假设，必须回到当前分支、真实调用链和真实测试去验证。**

---

## 工程上的经验

### 1. 先验证当前代码路径，再决定要不要改

Copilot 的评论有些对、有些过时，有些只在旧实现里成立。真正可靠的做法不是“看起来合理就改”，而是先确认：

- 这条代码现在还在不在
- 真实调用链是不是 review 里假设的那条
- 当前分支的测试有没有走到这条路径

### 2. 测试要覆盖真实入口，不要只测底层对象

这次 approval 的问题，底层 store 测试能过，但 coordinator / HTTP 入口还能暴露 bug。

经验是：

- 单测可以验证局部契约
- 但关键状态机一定要有“上层入口”测试
- 否则会出现“底层没问题，上层错了”的假安全感

### 3. 异步流程先定义契约，再改实现

这次反复确认了几个契约：

- timeout 是返回 `None`，还是抛异常
- duplicate response 是覆盖还是拒绝
- pending projection 谁负责维护
- session-scope approval 什么时候生效

这类问题如果不先定义清楚，修复很容易变成“这里补一下，那里漏一下”。

### 4. 真实集成测试要配套护栏

长链路 / 真实 HTTP / 子进程测试很有价值，但必须配套：

- backoff
- timeout
- terminate / kill 兜底
- 平台 guard

否则测试会自己变成不稳定源。

### 5. 提交要按“可回滚逻辑单元”拆

这次最稳的拆法不是按文件数，而是按逻辑单元：

- approval 响应语义
- live-server cleanup
- timeout contract
- Windows portability guard

这样 review、回滚、定位都更清晰。

---

## 需求上的经验

### 1. 这类需求本质上不是“修 comment”，而是“修语义”

真正要保证的是：

- approval 一次后，后续行为是否复用
- duplicate response 是否被拒绝
- HTTP / CLI / REPL 是否看到同一套状态

也就是说，需求核心是**状态语义一致**，不是单条评论本身。

### 2. approval 语义要有单一真相源

UI 里的 pending 只是 projection，真正的真相应该在 backend coordinator / store 里。

经验是：

- 真相源尽量单一
- projection 只做展示
- 不要让 UI 自己维护一套“可能过期”的状态

### 3. 跨平台支持要区分“能支持”和“值得支持”

这个仓库里 live-server 的 socket FD 传递是 POSIX 机制。
对 Windows 来说，最合理的处理不是强行改成另一个完全不同的测试路径，而是明确 skip。

这类判断要看：

- 该测试是不是在验证特定平台机制
- 改写后是否还在验证同一个行为
- 成本是否值得

---

## 最后总结

这次最值钱的经验可以压成一句话：

**把 review comment 当假设，把测试当证据，把当前分支代码当事实。**
