package demo.orders;

public class OrderNotFound extends RuntimeException {
    public OrderNotFound(String id) {
        super("Unknown order: " + id);
    }
}
