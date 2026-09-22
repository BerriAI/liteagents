use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyList, PyLong, PyString};

// PyBool is a PyLong subclass in CPython, so bool must be checked before
// int/float or every bool would also satisfy "integer"/"number".
fn type_matches(value: &Bound<'_, PyAny>, expected: &str) -> bool {
    match expected {
        "string" => value.is_instance_of::<PyString>(),
        "boolean" => value.is_instance_of::<PyBool>(),
        "integer" => !value.is_instance_of::<PyBool>() && value.is_instance_of::<PyLong>(),
        "number" => {
            !value.is_instance_of::<PyBool>()
                && (value.is_instance_of::<PyLong>() || value.is_instance_of::<PyFloat>())
        }
        "array" => value.is_instance_of::<PyList>(),
        "object" => value.is_instance_of::<PyDict>(),
        _ => true,
    }
}

#[pyfunction]
fn validate_tool_input(
    input_schema: &Bound<'_, PyDict>,
    input: &Bound<'_, PyDict>,
) -> PyResult<bool> {
    if let Some(required) = input_schema.get_item("required")? {
        let required_list = required.downcast::<PyList>().map_err(|_| {
            PyValueError::new_err("input_schema['required'] must be a list")
        })?;
        for field in required_list.iter() {
            let field_str: String = field.extract().map_err(|_| {
                PyValueError::new_err("input_schema['required'] entries must be strings")
            })?;
            if !input.contains(field_str.as_str())? {
                return Err(PyValueError::new_err(format!(
                    "missing required field: {field_str:?}"
                )));
            }
        }
    }

    if let Some(properties) = input_schema.get_item("properties")? {
        let properties_dict = properties.downcast::<PyDict>().map_err(|_| {
            PyValueError::new_err("input_schema['properties'] must be a dict")
        })?;
        for (field, spec) in properties_dict.iter() {
            let field_str: String = field.extract().map_err(|_| {
                PyValueError::new_err("input_schema['properties'] keys must be strings")
            })?;
            let value = match input.get_item(field_str.as_str())? {
                Some(v) => v,
                None => continue,
            };
            let spec_dict = match spec.downcast::<PyDict>() {
                Ok(d) => d,
                Err(_) => continue,
            };
            let expected_type = match spec_dict.get_item("type")? {
                Some(t) => t,
                None => continue,
            };
            let expected_type_str: String = match expected_type.extract() {
                Ok(s) => s,
                Err(_) => continue,
            };
            if !type_matches(&value, expected_type_str.as_str()) {
                return Err(PyValueError::new_err(format!(
                    "field {field_str:?} expected type {expected_type_str:?}"
                )));
            }
        }
    }

    Ok(true)
}

#[pyfunction]
fn normalize_content_blocks(blocks: &Bound<'_, PyList>) -> PyResult<Py<PyList>> {
    for block in blocks.iter() {
        let block_dict = block.downcast::<PyDict>().map_err(|_| {
            PyValueError::new_err(format!("content block must be a dict, got {block:?}"))
        })?;
        let type_value = block_dict.get_item("type")?.ok_or_else(|| {
            PyValueError::new_err(format!(
                "content block missing 'type': {block_dict:?}"
            ))
        })?;
        if !type_value.is_instance_of::<PyString>() {
            return Err(PyValueError::new_err(format!(
                "content block 'type' must be a string, got {type_value:?}"
            )));
        }
    }
    Ok(blocks.clone().unbind())
}

#[pyfunction]
fn append_history_entry(raw_history_json: &str, entry_json: &str) -> PyResult<String> {
    let mut history: serde_json::Value = serde_json::from_str(raw_history_json)
        .map_err(|e| PyValueError::new_err(format!("invalid history json: {e}")))?;
    let entry: serde_json::Value = serde_json::from_str(entry_json)
        .map_err(|e| PyValueError::new_err(format!("invalid entry json: {e}")))?;

    // Only an object entry belongs in a message-history array; anything else
    // is almost certainly a caller bug, so fail fast rather than append it.
    if !entry.is_object() {
        return Err(PyValueError::new_err(
            "entry_json must decode to a JSON object",
        ));
    }

    match history.as_array_mut() {
        Some(array) => array.push(entry),
        None => {
            return Err(PyValueError::new_err(
                "raw_history_json must decode to a JSON array",
            ))
        }
    }

    serde_json::to_string(&history)
        .map_err(|e| PyValueError::new_err(format!("failed to serialize history: {e}")))
}

#[pymodule]
fn _liteagents_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(validate_tool_input, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_content_blocks, m)?)?;
    m.add_function(wrap_pyfunction!(append_history_entry, m)?)?;
    Ok(())
}
