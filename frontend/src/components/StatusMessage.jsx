export default function StatusMessage({
  loading,
  error,
  loadingText = "Loading...",
  errorText,
  className = "hint",
  as: Component = "p",
}) {
  if (loading) {
    return <Component className={className}>{loadingText}</Component>;
  }
  if (error) {
    return <Component className={className}>{errorText || error}</Component>;
  }
  return null;
}
