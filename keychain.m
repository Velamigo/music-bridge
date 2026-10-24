#import <Foundation/Foundation.h>
#import <Security/Security.h>
int main(int argc, const char *argv[]) {
 @autoreleasepool {
  if (argc != 2) return 2;
  NSString *action = [NSString stringWithUTF8String:argv[1]];
  NSDictionary *query = @{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,
    (__bridge id)kSecAttrService:@"local.musicbridge.netease.session",
    (__bridge id)kSecAttrAccount:@"current-account"};
  if ([action isEqualToString:@"read"]) {
   NSMutableDictionary *q = [query mutableCopy];
   q[(__bridge id)kSecReturnData]=@YES;
   q[(__bridge id)kSecMatchLimit]=(__bridge id)kSecMatchLimitOne;
   CFTypeRef result=NULL; OSStatus status=SecItemCopyMatching((__bridge CFDictionaryRef)q,&result);
   if(status==errSecItemNotFound) return 3;
   if(status!=errSecSuccess) return 1;
   NSData *data=CFBridgingRelease(result);
   [[NSFileHandle fileHandleWithStandardOutput] writeData:data];
   return 0;
  }
  if ([action isEqualToString:@"delete"]) {
   OSStatus status=SecItemDelete((__bridge CFDictionaryRef)query);
   return status==errSecSuccess||status==errSecItemNotFound ? 0:1;
  }
  if ([action isEqualToString:@"write"]) {
   NSData *data=[[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
   if(!data.length || data.length>65536) return 2;
   OSStatus status=SecItemUpdate((__bridge CFDictionaryRef)query,(__bridge CFDictionaryRef)@{(__bridge id)kSecValueData:data});
   if(status==errSecItemNotFound) {
    NSMutableDictionary *q=[query mutableCopy];q[(__bridge id)kSecValueData]=data;
    q[(__bridge id)kSecAttrLabel]=@"音乐收集盒 · 网易云登录";
    status=SecItemAdd((__bridge CFDictionaryRef)q,NULL);
   }
   return status==errSecSuccess?0:1;
  }
  return 2;
 }
}
