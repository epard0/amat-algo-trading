import { Children, cloneElement, isValidElement, type ReactNode, type TableHTMLAttributes } from "react";

/** Preserve semantic tables on wide screens; retain every labeled value as records on narrow screens. */
export function tableText(node: ReactNode): string {
  return Children.toArray(node).map((child): string => {
    if (typeof child === "string" || typeof child === "number") return String(child);
    return isValidElement<{ children?: ReactNode }>(child) ? tableText(child.props.children) : "";
  }).join(" ").replace(/\s+/g, " ").trim();
}

export default function ReadableTable({ children, className = "", ...props }: TableHTMLAttributes<HTMLTableElement>) {
  const headings: string[] = [];
  const findHeadings = (nodes: ReactNode): void => {
    Children.forEach(nodes, (node) => {
      if (!isValidElement<{ children?: ReactNode }>(node)) return;
      if (node.type === "th") headings.push(tableText(node.props.children));
      else if (node.type !== "tbody") findHeadings(node.props.children);
    });
  };
  findHeadings(children);
  const annotate = (nodes: ReactNode): ReactNode => Children.map(nodes, (node) => {
    if (!isValidElement<{ children?: ReactNode; colSpan?: number; scope?: string; "data-label"?: string }>(node)) return node;
    if (node.type === "tr") {
      let column = 0;
      return cloneElement(node, {}, Children.map(node.props.children, (cell) => {
        if (!isValidElement<{ children?: ReactNode; colSpan?: number; scope?: string; "data-label"?: string }>(cell)) return cell;
        const index = column;
        column += cell.props.colSpan || 1;
        if (cell.type === "th") return cloneElement(cell, { scope: cell.props.scope || "col" });
        if (cell.type === "td") return cloneElement(cell, { "data-label": cell.props["data-label"] || headings[index] || "Details" }, <div className="readable-cell-value">{cell.props.children}</div>);
        return cell;
      }));
    }
    return node.props.children ? cloneElement(node, {}, annotate(node.props.children)) : node;
  });
  return <table role="table" {...props} className={`readable-table ${headings.length > 6 ? "readable-table-wide" : ""} ${className}`}>
    {annotate(children)}
  </table>;
}
