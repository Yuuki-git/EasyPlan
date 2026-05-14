import codecs

file_path = 'frontend/src/components/TaskBoard.tsx'
with codecs.open(file_path, 'r', 'utf-8', errors='ignore') as f:
    content = f.read()

start_idx = content.find('  return (\n    <motion.div\n      initial={{ opacity: 0, x: 20 }}')

if start_idx != -1:
    new_return = """  return (
    <motion.div 
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      className="fixed inset-0 bg-background flex z-40"
    >
      <Sidebar isOpen={sidebarOpen} toggle={() => setSidebarOpen(!sidebarOpen)} />
      
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        <header className="h-16 border-b border-muted/20 flex items-center px-4 shrink-0 bg-background/80 backdrop-blur-sm z-10 justify-between">
          <div className="flex items-center gap-4">
            <button 
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="text-muted-foreground hover:text-foreground transition-colors p-2 rounded-lg hover:bg-muted/20"
            >
              <Menu size={20} />
            </button>
            <h1 className="text-xl font-medium tracking-tight text-foreground">
              {currentViewBucket === 'my_day' ? '☀️ 我的一天' : '📅 计划中'}
            </h1>
          </div>
          
          <div className="flex items-center gap-4">
            <button 
              onClick={handleNewPlan}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors px-3 py-1.5 rounded-full hover:bg-muted/20"
            >
              {isGenerating ? '返回当前意图' : '新计划'}
            </button>
          </div>
        </header>
        
        <main className="flex-1 overflow-y-auto p-8 lg:px-24">
          <div className="max-w-3xl mx-auto pb-32">
            {isEmpty ? (
              <div className="flex flex-col items-center justify-center h-64 text-center space-y-4">
                <p className="text-muted-foreground/60 text-lg">
                  {currentViewBucket === 'planned' 
                    ? "您的专属空间空空如也。点击右上角，让 AI 为您分忧。"
                    : "今天的事情都搞定啦！去喝杯茶，享受生活吧 ☕️"}
                </p>
                {currentViewBucket === 'planned' && (
                  <button 
                    onClick={handleNewPlan}
                    className="px-4 py-2 border border-muted/50 rounded-lg text-sm text-foreground/70 hover:bg-muted/10 transition-colors"
                  >
                    {isGenerating ? '返回当前意图' : '新建意图'}
                  </button>
                )}
              </div>
            ) : (
              <BoardTaskNode node={displayTree} />
            )}
            
            <InlineTaskInput />
            
            {showFogOfWar && (
              <motion.div 
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="mt-12 flex justify-center"
              >
                <button 
                  onClick={handleGenerateNextPhase}
                  disabled={isGenerating}
                  className="group relative px-6 py-3 rounded-full overflow-hidden transition-all hover:scale-105 active:scale-95 disabled:opacity-60 disabled:hover:scale-100"
                >
                  <div className="absolute inset-0 bg-foreground/5 opacity-50 group-hover:opacity-100 transition-opacity" />
                  <motion.div 
                    animate={{ 
                      boxShadow: ['0px 0px 0px 0px rgba(168, 85, 247, 0)', '0px 0px 20px 2px rgba(168, 85, 247, 0.3)', '0px 0px 0px 0px rgba(168, 85, 247, 0)'] 
                    }}
                    transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
                    className="absolute inset-0 rounded-full border border-purple-500/30" 
                  />
                  <span className="relative text-sm font-medium text-purple-500/80 group-hover:text-purple-400 transition-colors flex items-center gap-2">
                    <Sparkles size={18} /> {isGenerating ? '正在生成下一阶段计划...' : '当前阶段已完成，让 AI 生成下一阶段计划'}
                  </span>
                </button>
              </motion.div>
            )}
          </div>
        </main>
      </div>
    </motion.div>
  );
};
"""
    with codecs.open(file_path, 'w', 'utf-8') as f:
        f.write(content[:start_idx] + new_return)
    print('Fixed corrupted JSX block via string truncation')
else:
    print('Could not find start idx')
