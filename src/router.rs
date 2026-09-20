//! Radix-tree based HTTP router.
//!
//! Uses the `matchit` crate for fast O(log n) route matching.

use matchit::Router as MatchitRouter;
use parking_lot::RwLock;
use std::collections::HashMap;
use std::sync::Arc;

/// Route information containing the handler ID and extracted parameters.
#[derive(Clone, Debug)]
pub struct RouteMatch {
    pub handler_id: usize,
    pub params: HashMap<String, String>,
}

/// Standard HTTP methods, each with a dedicated router slot. Indexing by slot
/// avoids hashing the method string on every request.
const STANDARD_METHODS: usize = 9;

/// Map a standard (uppercase) HTTP method to its slot index.
#[inline]
fn standard_method_index(method: &str) -> Option<usize> {
    Some(match method {
        "GET" => 0,
        "POST" => 1,
        "PUT" => 2,
        "DELETE" => 3,
        "PATCH" => 4,
        "OPTIONS" => 5,
        "HEAD" => 6,
        "TRACE" => 7,
        "CONNECT" => 8,
        _ => return None,
    })
}

/// HTTP method-based router using radix trees.
#[derive(Clone)]
pub struct Router {
    /// One radix tree per standard method (`None` until a route is added).
    routes: Arc<RwLock<[Option<MatchitRouter<usize>>; STANDARD_METHODS]>>,
    /// Non-standard methods, keyed by uppercased method name.
    other: Arc<RwLock<HashMap<String, MatchitRouter<usize>>>>,
}

impl Default for Router {
    fn default() -> Self {
        Self::new()
    }
}

impl Router {
    /// Create a new empty router.
    pub fn new() -> Self {
        Router {
            routes: Arc::new(RwLock::new(std::array::from_fn(|_| None))),
            other: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Extract a `RouteMatch` from a matched radix-tree node.
    #[inline]
    fn extract_match(router: &MatchitRouter<usize>, path: &str) -> Option<RouteMatch> {
        let matched = router.at(path).ok()?;
        // PERF: Pre-allocate HashMap with known param count
        let mut params: HashMap<String, String> = HashMap::with_capacity(matched.params.len());
        for (k, v) in matched.params.iter() {
            params.insert(k.to_owned(), v.to_owned());
        }
        Some(RouteMatch {
            handler_id: *matched.value,
            params,
        })
    }

    /// Add a route for a specific HTTP method.
    ///
    /// # Arguments
    /// * `method` - HTTP method (GET, POST, etc.)
    /// * `path` - URL path pattern with optional parameters (e.g., "/users/{id}")
    /// * `handler_id` - ID of the registered handler
    pub fn add_route(&mut self, method: &str, path: &str, handler_id: usize) -> Result<(), String> {
        let method = method.to_uppercase();
        // matchit 0.9 uses the `{param}` syntax natively, so the route is
        // inserted as-is (older versions required `:param`).
        if let Some(index) = standard_method_index(&method) {
            let mut routes = self.routes.write();
            let router = routes[index].get_or_insert_with(MatchitRouter::new);
            router
                .insert(path, handler_id)
                .map_err(|e| format!("Failed to add route: {e}"))
        } else {
            let mut other = self.other.write();
            let router = other.entry(method).or_default();
            router
                .insert(path, handler_id)
                .map_err(|e| format!("Failed to add route: {e}"))
        }
    }

    /// Match a request path against registered routes.
    ///
    /// # Arguments
    /// * `method` - HTTP method of the request
    /// * `path` - URL path to match
    ///
    /// # Returns
    /// * `Some(RouteMatch)` if a matching route is found
    /// * `None` if no route matches
    #[inline]
    pub fn match_route(&self, method: &str, path: &str) -> Option<RouteMatch> {
        // PERF: hyper supplies uppercase methods, so the common case is a direct
        // slot index — no method-string hashing or allocation.
        if let Some(index) = standard_method_index(method) {
            let routes = self.routes.read();
            return routes[index]
                .as_ref()
                .and_then(|router| Self::extract_match(router, path));
        }

        // Non-standard or lowercase method: normalize (off the hot path).
        let method = method.to_uppercase();
        if let Some(index) = standard_method_index(&method) {
            let routes = self.routes.read();
            return routes[index]
                .as_ref()
                .and_then(|router| Self::extract_match(router, path));
        }
        let other = self.other.read();
        other
            .get(&method)
            .and_then(|router| Self::extract_match(router, path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_routing() {
        let mut router = Router::new();
        router.add_route("GET", "/", 0).unwrap();
        router.add_route("GET", "/hello", 1).unwrap();
        router.add_route("POST", "/users", 2).unwrap();

        let match1 = router.match_route("GET", "/").unwrap();
        assert_eq!(match1.handler_id, 0);

        let match2 = router.match_route("GET", "/hello").unwrap();
        assert_eq!(match2.handler_id, 1);

        let match3 = router.match_route("POST", "/users").unwrap();
        assert_eq!(match3.handler_id, 2);

        assert!(router.match_route("DELETE", "/").is_none());
    }

    #[test]
    fn test_path_parameters() {
        let mut router = Router::new();

        // Test with curly-brace style params - these get converted to :param
        router.add_route("GET", "/users/{id}", 0).unwrap();
        router
            .add_route("GET", "/posts/{post_id}/comments/{comment_id}", 1)
            .unwrap();

        // Verify the router matches
        let match1 = router.match_route("GET", "/users/123");
        assert!(match1.is_some(), "Route should match /users/123");
        let match1 = match1.unwrap();
        assert_eq!(match1.handler_id, 0);
        assert_eq!(match1.params.get("id"), Some(&"123".to_string()));

        let match2 = router.match_route("GET", "/posts/456/comments/789");
        assert!(
            match2.is_some(),
            "Route should match /posts/456/comments/789"
        );
        let match2 = match2.unwrap();
        assert_eq!(match2.handler_id, 1);
        assert_eq!(match2.params.get("post_id"), Some(&"456".to_string()));
        assert_eq!(match2.params.get("comment_id"), Some(&"789".to_string()));
    }
}
