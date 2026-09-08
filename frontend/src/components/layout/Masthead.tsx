export default function Masthead() {
  return (
    <header className="flex items-center justify-between gap-5 mb-7">
      <div className="flex items-center gap-3.5">
        <div className="w-12 h-12 rounded-2xl grid place-items-center text-white bg-green shadow-brand font-black text-lg tracking-[-2px]">
          AI
        </div>
        <div>
          <h1 className="m-0 text-[22px] tracking-[-.5px]">AI信息小屋</h1>
          <p className="mt-1 text-muted text-[13px]">公开证据驱动的公众号原创助手</p>
        </div>
      </div>
      <nav className="flex gap-2.5 items-center max-sm:hidden">
        <a className="no-underline py-2 px-3.5 border border-line rounded-full bg-white/60 text-muted text-[13px]" href="/docs" target="_blank" rel="noreferrer">接口文档</a>
        <a className="no-underline py-2 px-3.5 border border-line rounded-full bg-white/60 text-muted text-[13px]" href="/health" target="_blank" rel="noreferrer">运行状态</a>
      </nav>
    </header>
  );
}
