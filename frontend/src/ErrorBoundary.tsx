import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

/** 防止未捕获渲染错误把整页打成白屏 / unexpected crash */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Enprato render error", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, fontFamily: "system-ui", maxWidth: 520 }}>
          <h1 style={{ fontSize: 18 }}>页面出错了，课还在</h1>
          <p style={{ color: "#6b7280" }}>{this.state.error.message}</p>
          <button
            type="button"
            onClick={() => {
              this.setState({ error: null });
              window.location.reload();
            }}
          >
            刷新重进
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
