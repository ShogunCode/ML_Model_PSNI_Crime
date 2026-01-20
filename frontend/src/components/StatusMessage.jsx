import PropTypes from "prop-types";

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

StatusMessage.propTypes = {
  loading: PropTypes.bool,
  error: PropTypes.oneOfType([PropTypes.string, PropTypes.instanceOf(Error)]),
  loadingText: PropTypes.string,
  errorText: PropTypes.string,
  className: PropTypes.string,
  as: PropTypes.elementType,
};
